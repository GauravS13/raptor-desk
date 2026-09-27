"""Demonstrate Measure, Doubt, Ask end to end on the fixture event (demo profile only).

1. Measure and doubt: compute the results and list the close calls.
2. Ask: approve the desk's proposals as the organizer.
3. Answer: enter one synthetic review per new assignment. Each is clearly
   labelled ``[demo]`` in its comment and in the audit trail. It scores every
   criterion at the panel's current mean for that project, shifted by that
   judge's measured leniency, as a consistent judge would.
4. Measure again, and report how each close call moved.

Real events never run this: reviews there come from judges.
"""

from dataclasses import dataclass
from statistics import mean

from apps.accounts.models import RoleGrant
from apps.events.models import Event
from apps.judging import close_calls, services
from apps.judging.repositories import reviews_for_organizer
from apps.judging.results import compute
from apps.judging.scoring import latest_reviews, score_map
from apps.seed.demo import require_demo_profile
from core import audit
from core.policy import Principal

DEMO_LABEL = "[demo] Synthetic review entered by manage.py demo_loop to show the close-call loop."


@dataclass(frozen=True)
class Movement:
    project: str
    cutoff: int
    before: float
    after: float

    @property
    def settled(self) -> bool:
        return not 0.2 < self.after < 0.8


def _organizer(event: Event) -> Principal:
    grant = RoleGrant.objects.filter(event=event, role="organizer").select_related("user").first()
    if grant is None:
        raise RuntimeError("The event has no organizer.")
    return Principal(user_id=grant.user_id, email=grant.user.email)


def _consensus(event: Event, project_id: str, flat: set[str]) -> dict[str, float]:
    reviews = [
        r
        for r in latest_reviews(list(reviews_for_organizer(event.pk)))
        if r.project_id == project_id and r.judge_id not in flat
    ]
    maps = [score_map(r) for r in reviews]
    keys = {key for m in maps for key in m}
    return {key: mean(m[key] for m in maps if key in m) for key in keys}


def run(event: Event, budget: int = close_calls.DEFAULT_BUDGET) -> list[Movement]:
    require_demo_profile()
    before = close_calls.report(event, budget=budget)
    chances = {(c.project.id, c.cutoff): c.p for c in before.close_calls}
    organizer = _organizer(event)
    created = close_calls.approve(organizer, event, budget=budget)

    diagnostics = {j.judge: j for j in before.results.evaluation.judges}
    flat = {j for j, d in diagnostics.items() if d.flat}
    criteria = {c.key: c for c in event.criteria.all()}
    for item in created:
        leniency = diagnostics[item.judge_id].severity if item.judge_id in diagnostics else 0.0
        scores = {}
        for key, value in _consensus(event, item.project_id, flat).items():
            criterion = criteria[key]
            scores[key] = int(
                min(max(round(value + leniency), criterion.min_score), criterion.max_score)
            )
        judge = Principal(user_id=item.judge_id, email="")
        review = services.save_review(
            judge,
            item,
            scores,
            comment=DEMO_LABEL,
            improvement="[demo] Not a real judgement.",
            submit=True,
        )
        audit.record(
            "demo.synthetic_review",
            f"[demo] Synthetic review of {item.project_id} entered for judge {item.judge_id}",
            actor=organizer,
            actor_role="organizer",
            event_id=event.pk,
            target=review,
        )

    after = compute(event).evaluation.by_project()
    return [
        Movement(project, cutoff, p, after[project].p_top[cutoff])
        for (project, cutoff), p in chances.items()
        if project in {item.project_id for item in created}
    ]
