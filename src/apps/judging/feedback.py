"""Feedback reports for teams, and "query a result".

After results are published, every team gets a report: its place in the
signed snapshot, its corrected score and interval, the judges' average per
criterion next to the event's average, and every written comment and
"one thing to improve". Judges are never named, and comments are listed in
a stable shuffled order, so the order does not hint at who wrote what.

A team member can flag a factual error ("a judge reviewed our old version",
"our video link was broken"). Organizers answer; both steps are audited and
emailed. Published results are never edited: the snapshot is signed.
"""

import hashlib
from collections import defaultdict
from dataclasses import dataclass, field
from statistics import mean

from django.conf import settings
from django.db import transaction

from apps.events.models import Event
from apps.judging.models import QueryStatus, ResultQuery
from apps.judging.publishing import published
from apps.judging.repositories import reviews_for_organizer
from apps.judging.scoring import latest_reviews, score_map
from apps.submissions import services as submission_services
from apps.submissions.models import Project
from apps.teams.models import TeamMember
from core import audit, clock, outbox
from core.http import conflict, not_found, unprocessable
from core.policy import Principal

MAX_OPEN_QUERIES = 3
MIN_MESSAGE = 20


@dataclass
class CriterionLine:
    key: str
    label: str
    weight: float
    scale: str
    team_mean: float | None
    event_mean: float | None


@dataclass
class Report:
    project: Project
    event: Event
    place: int
    of: int
    band: str
    score: float
    ci: tuple[float, float]
    reviews: int
    criteria: list[CriterionLine] = field(default_factory=list)
    comments: list[dict[str, str]] = field(default_factory=list)
    silent_reviews: int = 0
    queries: list[ResultQuery] = field(default_factory=list)


def can_read(principal: Principal, project: Project) -> bool:
    if principal.is_admin or principal.has_role("organizer", project.event_id):
        return True
    return submission_services.is_member(principal, project)


def _band(place: int, total: int) -> str:
    share = place / total
    for limit, label in ((0.1, "top 10%"), (0.25, "top quarter"), (0.5, "top half")):
        if share <= limit:
            return label
    return "second half"


def _shuffle_key(review_id: str) -> str:
    return hashlib.sha256(f"feedback-order:{review_id}".encode()).hexdigest()


def report(principal: Principal, project_id: str) -> Report:
    project = (
        Project.objects.select_related("event", "team", "canonical_version")
        .filter(pk=project_id)
        .first()
    )
    if project is None or not can_read(principal, project):
        raise not_found("No feedback report here.")
    event = project.event
    publication = published(event)
    if publication is None:
        raise conflict("not_published", "Feedback reports open when the results are published.")
    ranking = publication.snapshot.payload["ranking"]
    row = next((r for r in ranking if r["project"] == project.pk), None)
    if row is None:
        raise conflict("not_ranked", "This project is not in the published ranking.")

    reviews = latest_reviews(list(reviews_for_organizer(event.pk)))
    per_project: dict[str, list[dict[str, float]]] = defaultdict(list)
    for review in reviews:
        per_project[review.project_id].append(score_map(review))
    mine = [r for r in reviews if r.project_id == project.pk]

    lines = []
    for criterion in event.criteria.order_by("order", "key"):
        team_values = [s[criterion.key] for s in per_project[project.pk] if criterion.key in s]
        event_values = [
            s[criterion.key]
            for scores in per_project.values()
            for s in scores
            if criterion.key in s
        ]
        lines.append(
            CriterionLine(
                key=criterion.key,
                label=criterion.label,
                weight=float(criterion.weight),
                scale=f"{criterion.min_score} to {criterion.max_score}",
                team_mean=mean(team_values) if team_values else None,
                event_mean=mean(event_values) if event_values else None,
            )
        )

    comments = [
        {"comment": r.comment.strip(), "improvement": r.improvement.strip()}
        for r in sorted(mine, key=lambda r: _shuffle_key(r.pk))
        if r.comment.strip() or r.improvement.strip()
    ]
    return Report(
        project=project,
        event=event,
        place=row["place"],
        of=len(ranking),
        band=_band(row["place"], len(ranking)),
        score=row["score"],
        ci=(row["ci"][0], row["ci"][1]),
        reviews=len(mine),
        criteria=lines,
        comments=comments,
        silent_reviews=sum(1 for r in mine if not r.comment.strip()),
        queries=list(ResultQuery.objects.filter(project=project)),
    )


@transaction.atomic
def submit_query(principal: Principal, project_id: str, message: str) -> ResultQuery:
    found = report(principal, project_id)  # same access rule and "published" check
    if not submission_services.is_member(principal, found.project):
        raise not_found("Only the team can query its own result.")
    message = message.strip()
    if len(message) < MIN_MESSAGE:
        raise unprocessable("Describe the factual error in a sentence or two.")
    open_count = ResultQuery.objects.filter(project=found.project, status=QueryStatus.OPEN).count()
    if open_count >= MAX_OPEN_QUERIES:
        raise conflict("too_many_queries", "Wait for an answer to your open queries first.")
    query = ResultQuery.objects.create(
        event=found.event, project=found.project, author_id=principal.user_id, message=message
    )
    audit.record(
        "judging.result_queried",
        f"Team queried the result of {found.project.pk}",
        actor=principal,
        actor_role="participant",
        event_id=found.event.pk,
        target=query,
    )
    return query


@transaction.atomic
def answer_query(actor: Principal, event: Event, query_id: str, response: str) -> ResultQuery:
    query = ResultQuery.objects.select_related("author").filter(event=event, pk=query_id).first()
    if query is None:
        raise not_found("No such query.")
    if query.status != QueryStatus.OPEN:
        raise conflict("already_answered", "This query has been answered.")
    response = response.strip()
    if len(response) < MIN_MESSAGE:
        raise unprocessable("Write an answer the team can act on.")
    query.status = QueryStatus.ANSWERED
    query.response = response
    query.responded_by_id = actor.user_id or ""
    query.responded_at = clock.now()
    query.save(update_fields=["status", "response", "responded_by_id", "responded_at"])
    audit.record(
        "judging.query_answered",
        f"Answered the query on {query.project_id}",
        actor=actor,
        actor_role="organizer",
        event_id=event.pk,
        target=query,
    )
    outbox.enqueue(
        "email",
        {
            "to": [query.author.email],
            "subject": f"Your query about {event.name} results has an answer",
            "body": (
                f"The organizers answered your query:\n\n{response}\n\n"
                f"Your feedback report: {settings.BASE_URL}/projects/{query.project_id}/feedback\n"
            ),
        },
        dedupe_key=f"query-answered:{query.pk}",
    )
    return query


def notify_teams(event: Event) -> int:
    """Email every team member a link to their feedback report (once per project)."""
    members = TeamMember.objects.filter(event=event).select_related("user", "team")
    projects = {p.team_id: p.pk for p in Project.objects.filter(event=event, status="submitted")}
    sent = 0
    for member in members:
        project_id = projects.get(member.team_id)
        if project_id is None:
            continue
        outbox.enqueue(
            "email",
            {
                "to": [member.user.email],
                "subject": f"{event.name}: results and your feedback report",
                "body": (
                    f"The results of {event.name} are published: "
                    f"{settings.BASE_URL}/events/{event.pk}/results\n\n"
                    f"Your team's feedback report, with every judge's written feedback:\n"
                    f"{settings.BASE_URL}/projects/{project_id}/feedback\n"
                ),
            },
            dedupe_key=f"feedback-ready:{event.pk}:{member.user_id}",
        )
        sent += 1
    return sent
