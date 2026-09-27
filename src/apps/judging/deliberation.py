"""The deliberation board: the computed ranking, what is still in doubt, and what people decided.

The Fairness Engine proposes an order; people decide the prizes. Every
decision carries a written rationale and is append-only:

- **confirm**: keep a project at its computed place. This settles a close call
  by judgement when more evidence is not available.
- **place above**: move a project directly above another one.

The final order is the computed order with the decisions applied in the order
they were made. DOGFOOD's own rule is applied automatically: an exact tie on
the corrected score is broken by bonus points, and the board says where that
happened.
"""

from dataclasses import dataclass, field
from itertools import pairwise

from django.db import transaction

from apps.events.models import Event, Phase
from apps.judging.models import DecisionKind, RankingDecision
from apps.judging.results import DEFAULT_CUTOFFS, EventResults, compute
from core import audit
from core.http import conflict, unprocessable
from core.policy import Principal

OPEN_PHASES = (Phase.JUDGING, Phase.DELIBERATION)
MIN_RATIONALE = 10


@dataclass
class BoardRow:
    final_rank: int
    computed_rank: int
    project_id: str
    name: str
    team: str
    score: float
    bonus: float
    ci_low: float
    ci_high: float
    p_top: dict[int, float]
    flags: list[str]
    notes: list[str] = field(default_factory=list)
    decided: bool = False

    def close_for(self, places: int) -> bool:
        """A close call on any cutoff inside the prize places."""
        return any(
            flag.startswith("close_call_top") and int(flag.removeprefix("close_call_top")) <= places
            for flag in self.flags
        )


@dataclass
class Board:
    results: EventResults
    rows: list[BoardRow]
    decisions: list[RankingDecision]
    prize_places: int

    @property
    def undecided(self) -> list[BoardRow]:
        """Close calls on the prize places that nobody has ruled on yet."""
        return [r for r in self.rows if r.close_for(self.prize_places) and not r.decided]

    @property
    def final_order(self) -> list[str]:
        return [row.project_id for row in self.rows]


def prize_places(event: Event) -> int:
    ranks = list(event.prizes.values_list("rank", flat=True))
    return max(ranks) if ranks else max(DEFAULT_CUTOFFS)


def apply_decisions(order: list[str], decisions: list[RankingDecision]) -> list[str]:
    final = list(order)
    for decision in decisions:
        if decision.kind != DecisionKind.PLACE_ABOVE:
            continue
        if decision.project_id not in final or decision.other_id not in final:
            continue
        final.remove(decision.project_id)
        final.insert(final.index(decision.other_id), decision.project_id)
    return final


def board(event: Event, results: EventResults | None = None) -> Board:
    results = results if results is not None and results.method == "additive" else None
    results = results or compute(event)
    method = results.method
    projects = {p.project: p for p in results.evaluation.projects}
    computed = results.evaluation.ranking(method)
    decisions = list(RankingDecision.objects.filter(event=event))
    decided = {d.project_id for d in decisions} | {d.other_id for d in decisions if d.other_id}
    final = apply_decisions(computed, decisions)

    rows = []
    for position, project_id in enumerate(final, start=1):
        p = projects[project_id]
        rows.append(
            BoardRow(
                final_rank=position,
                computed_rank=p.ranks[method],
                project_id=project_id,
                name=results.names.get(project_id, project_id),
                team=results.teams.get(project_id, ""),
                score=p.scores[method],
                bonus=results.bonus.get(project_id, 0.0),
                ci_low=p.ci_low,
                ci_high=p.ci_high,
                p_top=p.p_top,
                flags=p.flags,
                decided=project_id in decided,
            )
        )

    explain_ties(rows)
    for row in rows:
        if row.final_rank != row.computed_rank:
            row.notes.append(f"moved from computed place {row.computed_rank} by decision")
    return Board(results, rows, decisions, prize_places(event))


def explain_ties(rows: list[BoardRow]) -> None:
    """Where the published tie-break rule decided an exact tie, say so on the upper row."""
    by_rank = sorted(rows, key=lambda r: r.computed_rank)
    for upper, lower in pairwise(by_rank):
        if round(upper.score, 9) != round(lower.score, 9):
            continue
        if upper.bonus != lower.bonus:
            note = (
                f"exact tie with {lower.project_id} broken by bonus points "
                f"({upper.bonus:g} against {lower.bonus:g})"
            )
        else:
            note = f"exact tie with {lower.project_id}, equal bonus: ordered by id; decide it"
        upper.notes.append(note)


@transaction.atomic
def record_decision(
    actor: Principal,
    event: Event,
    *,
    kind: str,
    project_id: str,
    rationale: str,
    other_id: str | None = None,
) -> RankingDecision:
    if event.phase not in OPEN_PHASES:
        raise conflict(
            "deliberation_closed", "Decisions can be recorded during judging and deliberation."
        )
    rationale = rationale.strip()
    if len(rationale) < MIN_RATIONALE:
        raise unprocessable("Write down why: the rationale is part of the record.")
    if kind not in DecisionKind.values:
        raise unprocessable("Unknown kind of decision.", {"kind": kind})
    ranked = set(board(event).final_order)
    if project_id not in ranked:
        raise unprocessable("That project is not in the ranking.", {"project_id": project_id})
    if kind == DecisionKind.PLACE_ABOVE:
        if not other_id or other_id not in ranked or other_id == project_id:
            raise unprocessable("Choose a different ranked project to place it above.")
    else:
        other_id = None

    decision = RankingDecision.objects.create(
        event=event,
        kind=kind,
        project_id=project_id,
        other_id=other_id,
        rationale=rationale,
        actor_id=actor.user_id or "",
    )
    summary = (
        f"Placed {project_id} directly above {other_id}"
        if kind == DecisionKind.PLACE_ABOVE
        else f"Confirmed {project_id} at its computed place"
    )
    audit.record(
        "judging.decision_recorded",
        f"{summary}: {rationale}",
        actor=actor,
        actor_role="organizer",
        event_id=event.pk,
        target=decision,
        details={"kind": kind, "project": project_id, "other": other_id},
    )
    return decision
