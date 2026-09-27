"""Judge score reads (isolation enforced in the repository layer) and CSV exports."""

from datetime import datetime

from django.http import HttpRequest, HttpResponse
from ninja import Router, Schema

from apps.events import policies as event_policies
from apps.events.services import get_event
from apps.judging import exports, repositories
from apps.judging.models import Review
from core.http import not_found
from core.policy import Rule, define, get_principal, policy

JUDGE_SELF = define(
    "judging.self",
    Rule(
        roles=frozenset({"judge"}),
        event_scoped=False,
        description="Judges read their own scores, and nobody else's.",
    ),
)
JUDGE_SCORES = define(
    "judging.judge_scores",
    Rule(
        authenticated=True,
        description="A judge's scores: that judge, the event's organizers or an admin only.",
    ),
)

router = Router(tags=["judging"])


class ScoreOut(Schema):
    criterion: str
    value: int


class ReviewOut(Schema):
    review_id: str
    event_id: str
    project_id: str
    project: str
    version: int
    judge_id: str
    status: str
    scores: list[ScoreOut]
    comment: str
    submitted_at: datetime | None


def review_out(review: Review) -> ReviewOut:
    return ReviewOut(
        review_id=review.pk,
        event_id=review.event_id,
        project_id=review.project_id,
        project=review.version.name,
        version=review.version.n,
        judge_id=review.judge_id,
        status=review.status,
        scores=[
            ScoreOut(criterion=item.criterion.key, value=item.value)
            for item in sorted(review.scores.all(), key=lambda s: s.criterion.order)
        ],
        comment=review.comment,
        submitted_at=review.submitted_at,
    )


@router.get("/judge/scores", response=list[ReviewOut])
@policy(JUDGE_SELF)
def my_scores(request: HttpRequest, event: str = "") -> list[ReviewOut]:
    """The signed-in judge's own reviews and scores."""
    reviews = repositories.reviews_for_self(get_principal(request), event or None)
    return [review_out(review) for review in reviews]


@router.get("/judges/{judge_id}/scores", response=list[ReviewOut])
@policy(JUDGE_SCORES)
def judge_scores(request: HttpRequest, judge_id: str, event: str = "") -> list[ReviewOut]:
    """One judge's scores. Another judge asking gets 403, whatever the event."""
    reviews = repositories.reviews_of_judge(get_principal(request), judge_id, event or None)
    return [review_out(review) for review in reviews]


@router.get("/events/{event_id}/exports/{kind}.csv")
@policy(event_policies.EVENTS_MANAGE)
def export_csv(request: HttpRequest, event_id: str, kind: str) -> HttpResponse:
    """CSV export: results, reviews, assignments, teams, submissions, registrations or audit."""
    export = exports.EXPORTS.get(kind)
    if export is None:
        raise not_found(f"Unknown export. Choose one of: {', '.join(sorted(exports.EXPORTS))}.")
    event = get_event(event_id)
    response = HttpResponse(export(event), content_type="text/csv; charset=utf-8")
    response["Content-Disposition"] = f'attachment; filename="{event.slug}-{kind}.csv"'
    return response
