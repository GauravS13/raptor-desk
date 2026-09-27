"""Measure, Doubt, Ask on an event's live data.

- **Measure and Doubt** come from ``results.compute``: the corrected ranking, and
  each project's chance of finishing inside every prize cutoff. A chance between
  0.2 and 0.8 makes the project a close call.
- **Ask** proposes the few extra reviews that would settle those calls
  (``scoring_engine.ask``), within a budget the organizer sets.

Once approved, the proposals become ordinary assignments that judges see in
their queue. When the reviews arrive, the next computation shows whether the
call is settled. A project whose asked-for reviews are still outstanding is
listed as waiting and is not asked about again.
"""

from collections import Counter, defaultdict
from dataclasses import dataclass

from django.db import transaction

from apps.events.models import Event, Phase
from apps.judging import services
from apps.judging.models import Assignment, AssignmentSource, AssignmentStatus
from apps.judging.results import EventResults, compute
from apps.submissions.models import Project
from core import audit
from core.http import conflict, unprocessable
from core.policy import Principal
from scoring_engine import ask
from scoring_engine.assignment import JudgeInput, ProjectInput, Proposal

REVIEWS_PER_CLOSE_CALL = 2
DEFAULT_BUDGET = 6


@dataclass
class CloseCallReport:
    results: EventResults
    close_calls: list[ask.CloseCall]  # one per project, most uncertain first
    waiting: dict[str, list[str]]  # project -> judges asked whose review is not in yet
    proposals: list[Proposal]
    shortfalls: dict[str, int]
    budget: int

    @property
    def statuses(self) -> dict[str, str]:
        """What is happening about each close call, in words."""
        proposed = Counter(p.project for p in self.proposals)
        out = {}
        for call in self.close_calls:
            project = call.project.id
            if project in self.waiting:
                out[project] = f"waiting for {len(self.waiting[project])} review(s)"
            elif proposed[project]:
                out[project] = f"{proposed[project]} review(s) proposed"
            elif project in self.shortfalls:
                out[project] = "no eligible judge left"
            else:
                out[project] = "outside this budget"
        return out

    @property
    def unaskable(self) -> list[str]:
        proposed = {p.project for p in self.proposals}
        return [p for p in self.shortfalls if p not in proposed]


def outstanding(event: Event) -> dict[str, list[str]]:
    rows = (
        Assignment.objects.filter(
            event=event, source=AssignmentSource.CLOSE_CALL, status=AssignmentStatus.ACTIVE
        )
        .exclude(reviews__status="submitted")
        .values_list("project_id", "judge_id")
        .order_by("project_id", "judge_id")
    )
    waiting: dict[str, list[str]] = defaultdict(list)
    for project_id, judge_id in rows:
        waiting[project_id].append(judge_id)
    return dict(waiting)


def report(
    event: Event, *, budget: int = DEFAULT_BUDGET, results: EventResults | None = None
) -> CloseCallReport:
    results = results if results is not None and results.method == "additive" else None
    results = results or compute(event)
    evaluation = results.evaluation
    projects = {p.pk: p for p in Project.objects.filter(event=event, status="submitted")}

    calls = []
    for result in evaluation.projects:
        project = projects.get(result.project)
        if project is None:
            continue
        target = ProjectInput(project.pk, project.track_id, project.team_id)
        calls.extend(
            ask.CloseCall(target, cutoff, result.p_top[cutoff])
            for cutoff in evaluation.cutoffs
            if f"close_call_top{cutoff}" in result.flags
        )
    waiting = outstanding(event)

    # Every judge who ever had the project, including withdrawn assignments, is
    # not asked again; only live assignments count towards a judge's load.
    reviewers: dict[str, set[str]] = defaultdict(set)
    load: Counter[str] = Counter()
    for judge_id, project_id, status in Assignment.objects.filter(event=event).values_list(
        "judge_id", "project_id", "status"
    ):
        reviewers[project_id].add(judge_id)
        if status != AssignmentStatus.WITHDRAWN:
            load[judge_id] += 1

    tracks = services.tracks_of(event)
    plan = ask.propose(
        [c for c in calls if c.project.id not in waiting],
        [JudgeInput(judge, tracks[judge]) for judge in sorted(tracks)],
        reviewers=reviewers,
        max_load=event.max_load_per_judge,
        load=dict(load),
        experience={j.judge: j.n for j in evaluation.judges},
        conflicts=services.conflict_pairs(event),
        exclude={j.judge for j in evaluation.judges if j.flat},
        per_project=REVIEWS_PER_CLOSE_CALL,
        budget=budget,
    )
    return CloseCallReport(
        results=results,
        close_calls=ask.most_uncertain(calls),
        waiting=waiting,
        proposals=plan.proposals,
        shortfalls=plan.shortfalls,
        budget=budget,
    )


@transaction.atomic
def approve(
    actor: Principal,
    event: Event,
    *,
    budget: int = DEFAULT_BUDGET,
    pairs: list[tuple[str, str]] | None = None,
) -> list[Assignment]:
    """Turn the current proposals (or a chosen subset) into assignments and notify the judges."""
    if event.phase != Phase.JUDGING:
        raise conflict("not_judging", "Extra reviews can only be requested while judging is open.")
    current = report(event, budget=budget)
    chosen = current.proposals
    if pairs is not None:
        known = {(p.judge, p.project) for p in current.proposals}
        unknown = sorted(set(pairs) - known)
        if unknown:
            raise unprocessable(
                "These are not current proposals; reload the results and try again.",
                {"pairs": [{"judge_id": j, "project_id": p} for j, p in unknown]},
            )
        chosen = [p for p in current.proposals if (p.judge, p.project) in set(pairs)]

    created = [
        Assignment.objects.create(
            event=event,
            judge_id=proposal.judge,
            project_id=proposal.project,
            source=AssignmentSource.CLOSE_CALL,
            status=AssignmentStatus.ACTIVE,
            reason=proposal.reason[:300],
            queue_position=services.random_queue_position(),
            created_by_id=actor.user_id or "",
        )
        for proposal in chosen
    ]
    if not created:
        return []
    per_judge = Counter(item.judge_id for item in created)
    for judge_id, count in per_judge.items():
        services.notify_judge(event, judge_id, count)
    per_project = Counter(item.project_id for item in created)
    listed = ", ".join(f"{project} ({n})" for project, n in sorted(per_project.items()))
    audit.record(
        "judging.close_calls_asked",
        f"Asked for {len(created)} extra review(s) to settle close calls: {listed}",
        actor=actor,
        actor_role="organizer",
        event_id=event.pk,
        details={
            "budget": budget,
            "assignments": [
                {"judge": a.judge_id, "project": a.project_id, "reason": a.reason} for a in created
            ],
        },
    )
    return created
