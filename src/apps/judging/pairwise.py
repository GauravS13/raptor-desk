"""Pairwise mode on an event: judges answer "which is stronger?", Bradley-Terry ranks.

A judge compares two projects they were assigned (so they have seen both, and
no conflict can slip in). Each judge answers each pair once. The next pair
offered is the most informative one left: pairs involving a close call on the
prize places first, then the pair whose outcome the current fit predicts
least well. Every comparison is also written to the event's signed score
ledger.

The pairwise standings sit beside the score-based ranking. They never replace
it, and where the two disagree the organizer sees it.
"""

from dataclasses import dataclass

from django.db import IntegrityError, transaction

from apps.events.models import Event, Phase
from apps.judging import ledger
from apps.judging.models import Assignment, PairwiseComparison, PairwiseOutcome
from apps.judging.results import compute
from apps.submissions.models import Project
from core import audit
from core.http import conflict, forbidden, not_found, unprocessable
from core.policy import Principal
from scoring_engine import pairwise as bt
from scoring_engine.simulate import spearman


def _assigned(event: Event, judge_id: str) -> list[str]:
    return sorted(
        Assignment.objects.filter(event=event, judge_id=judge_id, project__status="submitted")
        .exclude(status="withdrawn")
        .values_list("project_id", flat=True)
    )


def comparisons(event: Event) -> list[bt.Comparison]:
    return [
        bt.Comparison(c.project_a_id, c.project_b_id, c.outcome, c.judge_id)
        for c in PairwiseComparison.objects.filter(event=event)
    ]


def _close_calls(event: Event) -> set[str]:
    evaluation = compute(event, bootstrap=100).evaluation
    return {
        p.project for p in evaluation.projects if any(f.startswith("close_call") for f in p.flags)
    }


@dataclass(frozen=True)
class NextPair:
    a: Project
    b: Project
    done: int
    possible: int


def next_for(principal: Principal, event: Event) -> NextPair | None:
    mine = _assigned(event, principal.user_id or "")
    done = {
        frozenset((c.project_a_id, c.project_b_id))
        for c in PairwiseComparison.objects.filter(event=event, judge_id=principal.user_id)
    }
    possible = len(mine) * (len(mine) - 1) // 2
    strengths = bt.fit(comparisons(event), mine)
    pair = bt.next_pair(mine, done, strengths, priority=_close_calls(event))
    if pair is None:
        return None
    projects = Project.objects.select_related("canonical_version", "team").in_bulk(list(pair))
    return NextPair(projects[pair[0]], projects[pair[1]], len(done), possible)


def record(
    principal: Principal, event: Event, first: str, second: str, outcome: str
) -> PairwiseComparison:
    """Store one answer. ``outcome`` is "a", "b" or "tie" for (first, second) as shown."""
    if event.phase != Phase.JUDGING:
        raise conflict("judging_closed", "Comparisons can only be made while judging is open.")
    if outcome not in PairwiseOutcome.values:
        raise unprocessable("The answer must be a, b or tie.")
    if first == second:
        raise unprocessable("Compare two different projects.")
    mine = set(_assigned(event, principal.user_id or ""))
    if first not in mine or second not in mine:
        raise forbidden("You can only compare projects you were assigned.")
    low, high = sorted((first, second))
    if (first, second) != (low, high):  # stored with the smaller id first
        outcome = {"a": "b", "b": "a"}.get(outcome, outcome)
    try:
        with transaction.atomic():
            comparison = PairwiseComparison.objects.create(
                event=event,
                judge_id=principal.user_id,
                project_a_id=low,
                project_b_id=high,
                outcome=outcome,
            )
            ledger.append_comparison(comparison)
    except IntegrityError as exc:
        raise conflict("already_compared", "You already compared these two projects.") from exc
    audit.record(
        "pairwise.compared",
        f"Judge compared {low} and {high}",
        actor=principal,
        actor_role="judge",
        event_id=event.pk,
        target=comparison,
    )
    return comparison


@dataclass(frozen=True)
class PairwiseTable:
    standings: list[bt.Standing]
    names: dict[str, str]
    total: int
    judges: int
    agreement: float | None  # Spearman with the score-based ranking, on compared projects


def table(event: Event) -> PairwiseTable:
    data = comparisons(event)
    projects = Project.objects.filter(event=event, status="submitted").select_related(
        "canonical_version"
    )
    names = {p.pk: (p.canonical_version.name if p.canonical_version else p.pk) for p in projects}
    compared = sorted({c.a for c in data} | {c.b for c in data})
    rows = bt.standings(data, compared) if data else []
    agreement = None
    if len(compared) >= 3:
        scores = {
            p.project: p.scores["additive"] for p in compute(event, bootstrap=0).evaluation.projects
        }
        strengths = {s.project: s.strength for s in rows if s.project in scores}
        if len(strengths) >= 3:
            agreement = spearman(strengths, scores)
    return PairwiseTable(rows, names, len(data), len({c.judge for c in data}), agreement)


def project_for(event_id: str, project_id: str) -> Project:
    project = Project.objects.filter(event_id=event_id, pk=project_id).first()
    if project is None:
        raise not_found("No such project.")
    return project
