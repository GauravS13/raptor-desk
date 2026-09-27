"""Assignment, reviewing and progress. Shared by the judging pages and the REST API."""

import secrets
from dataclasses import dataclass
from typing import Any

from django.conf import settings
from django.db import transaction
from django.db.models import Max

from apps.accounts.models import JudgeProfile, RoleGrant, User
from apps.events.models import Event, Phase
from apps.judging import ledger
from apps.judging.models import Assignment, Conflict, JudgeTrack, Review, ScoreItem
from apps.submissions.models import Project
from apps.teams.models import TeamMember
from core import audit, clock, outbox
from core.http import conflict, not_found, unprocessable
from core.policy import Principal
from scoring_engine import assignment as planner

# --- Eligibility ---------------------------------------------------------------


def judge_ids(event: Event) -> list[str]:
    return list(
        RoleGrant.objects.filter(event=event, role="judge").values_list("user_id", flat=True)
    )


def tracks_of(event: Event) -> dict[str, frozenset[str]]:
    tracks: dict[str, set[str]] = {judge: set() for judge in judge_ids(event)}
    for judge_id, track_id in JudgeTrack.objects.filter(event=event).values_list(
        "judge_id", "track_id"
    ):
        tracks.setdefault(judge_id, set()).add(track_id)
    return {judge: frozenset(t) for judge, t in tracks.items()}


def conflict_pairs(event: Event) -> set[tuple[str, str]]:
    """(judge, team) pairs a judge must never review: declared, membership, or listed emails."""
    pairs = set(Conflict.objects.filter(event=event).values_list("judge_id", "team_id"))
    judges = set(judge_ids(event))
    members = TeamMember.objects.filter(event=event).select_related("user")
    for member in members:
        if member.user_id in judges:
            pairs.add((member.user_id, member.team_id))
    profiles = JudgeProfile.objects.filter(user_id__in=judges)
    for profile in profiles:
        emails = {e.lower() for e in profile.coi_emails}
        domains = {d.lower().lstrip("@") for d in profile.coi_domains}
        for member in members:
            email = member.user.email
            if email in emails or email.split("@")[-1] in domains:
                pairs.add((profile.user_id, member.team_id))
    return pairs


def _project_inputs(event: Event) -> list[planner.ProjectInput]:
    return [
        planner.ProjectInput(id=p.pk, track=p.track_id, team=p.team_id)
        for p in Project.objects.filter(event=event, status="submitted")
    ]


def _check_assignable(event: Event, judge_id: str, project: Project) -> None:
    tracks = tracks_of(event)
    if judge_id not in tracks:
        raise unprocessable("That person is not a judge in this event.")
    candidate = planner.JudgeInput(judge_id, tracks[judge_id])
    target = planner.ProjectInput(project.pk, project.track_id, project.team_id)
    if (judge_id, project.team_id) in conflict_pairs(event):
        raise conflict("conflict_of_interest", "This judge has a conflict with that team.")
    if not planner.eligible(candidate, target, set()):
        raise conflict("other_track", "This judge does not cover that project's track.")


def random_queue_position() -> int:
    """Random per assignment, so the order a judge sees projects in is not systematic."""
    return secrets.randbelow(1_000_000)


# --- Assignment ---------------------------------------------------------------


@transaction.atomic
def assign_batch(
    actor: Principal, event: Event, judge_id: str, project_ids: list[str]
) -> list[Assignment]:
    projects = {p.pk: p for p in Project.objects.filter(event=event, pk__in=project_ids)}
    missing = sorted(set(project_ids) - set(projects))
    if missing:
        raise unprocessable("Unknown projects for this event.", {"projects": missing})
    created = []
    for project_id in project_ids:
        project = projects[project_id]
        _check_assignable(event, judge_id, project)
        if Assignment.objects.filter(judge_id=judge_id, project=project).exists():
            raise conflict("already_assigned", f"Already assigned: {project_id}.")
        created.append(
            Assignment.objects.create(
                event=event,
                judge_id=judge_id,
                project=project,
                source="batch",
                status="active",
                reason="Assigned by an organizer",
                queue_position=random_queue_position(),
                created_by_id=actor.user_id or "",
            )
        )
    audit.record(
        "judging.assigned",
        f"Assigned {len(created)} project(s) to judge {judge_id}",
        actor=actor,
        actor_role="organizer",
        event_id=event.pk,
        details={"judge": judge_id, "projects": project_ids},
    )
    notify_judge(event, judge_id, len(created))
    return created


@dataclass
class PlanResult:
    proposed: list[Assignment]
    shortfalls: dict[str, int]


@transaction.atomic
def propose_assignments(actor: Principal, event: Event) -> PlanResult:
    """Replace the current proposals with a fresh plan. Nothing is visible to judges yet."""
    Assignment.objects.filter(event=event, status="proposed").delete()
    existing = set(
        Assignment.objects.filter(event=event)
        .exclude(status="withdrawn")
        .values_list("judge_id", "project_id")
    )
    tracks = tracks_of(event)
    result = planner.plan(
        _project_inputs(event),
        [planner.JudgeInput(judge, tracks[judge]) for judge in sorted(tracks)],
        reviews_per_project=event.reviews_per_project,
        max_load=event.max_load_per_judge,
        existing=existing,
        conflicts=conflict_pairs(event),
    )
    proposed = [
        Assignment.objects.create(
            event=event,
            judge_id=p.judge,
            project_id=p.project,
            source="auto",
            status="proposed",
            reason=p.reason,
            queue_position=random_queue_position(),
            created_by_id=actor.user_id or "",
        )
        for p in result.proposals
    ]
    audit.record(
        "judging.plan_proposed",
        f"Proposed {len(proposed)} assignments; {sum(result.shortfalls.values())} reviews "
        "could not be placed",
        actor=actor,
        actor_role="organizer",
        event_id=event.pk,
        details={"shortfalls": result.shortfalls},
    )
    return PlanResult(proposed=proposed, shortfalls=result.shortfalls)


@transaction.atomic
def publish_proposals(actor: Principal, event: Event) -> int:
    proposed = Assignment.objects.filter(event=event, status="proposed")
    per_judge: dict[str, int] = {}
    for item in proposed:
        per_judge[item.judge_id] = per_judge.get(item.judge_id, 0) + 1
    count = proposed.update(status="active")
    for judge_id, n in per_judge.items():
        notify_judge(event, judge_id, n)
    audit.record(
        "judging.plan_published",
        f"Published {count} proposed assignments",
        actor=actor,
        actor_role="organizer",
        event_id=event.pk,
    )
    return count


@transaction.atomic
def withdraw_assignment(actor: Principal, event: Event, assignment_id: str) -> Assignment:
    item = Assignment.objects.filter(event=event, pk=assignment_id).first()
    if item is None:
        raise not_found("No such assignment.")
    if item.reviews.filter(status="submitted").exists():
        raise conflict("review_submitted", "This judge already submitted a review.")
    item.status = "withdrawn"
    item.save(update_fields=["status"])
    audit.record(
        "judging.withdrawn",
        f"Withdrew assignment of {item.project_id} from judge {item.judge_id}",
        actor=actor,
        actor_role="organizer",
        event_id=event.pk,
        target=item,
    )
    return item


@transaction.atomic
def declare_conflict(
    actor: Principal, event: Event, judge_id: str, team_id: str, reason: str = ""
) -> Conflict:
    if not event.teams.filter(pk=team_id).exists():
        raise unprocessable("That team does not belong to this event.")
    record, _ = Conflict.objects.get_or_create(
        event=event,
        judge_id=judge_id,
        team_id=team_id,
        defaults={"reason": reason, "declared_by_id": actor.user_id or ""},
    )
    Assignment.objects.filter(
        event=event, judge_id=judge_id, project__team_id=team_id, status__in=["proposed", "active"]
    ).update(status="withdrawn")
    audit.record(
        "judging.conflict_declared",
        f"Conflict declared between judge {judge_id} and team {team_id}",
        actor=actor,
        event_id=event.pk,
        details={"reason": reason},
    )
    return record


def notify_judge(event: Event, judge_id: str, count: int) -> None:
    user = User.objects.filter(pk=judge_id).first()
    if user is None or count == 0:
        return
    outbox.enqueue(
        "email",
        {
            "to": [user.email],
            "subject": f"{count} project(s) to review for {event.name}",
            "body": (
                f"You have {count} new project(s) to review for {event.name}.\n\n"
                f"Open your queue: {settings.BASE_URL}/judge\n"
            ),
        },
    )


# --- Reviewing ----------------------------------------------------------------


def queue(principal: Principal, event_id: str | None = None) -> list[Assignment]:
    items = Assignment.objects.filter(
        judge_id=principal.user_id, status__in=["active", "done"]
    ).select_related("project__canonical_version", "project__track", "project__team", "event")
    if event_id:
        items = items.filter(event_id=event_id)
    return list(items.order_by("event_id", "queue_position"))


def own_assignment(principal: Principal, assignment_id: str) -> Assignment:
    item = (
        Assignment.objects.select_related("event", "project__canonical_version", "project__team")
        .filter(pk=assignment_id)
        .first()
    )
    if (
        item is None
        or item.judge_id != principal.user_id
        or item.status in ("proposed", "withdrawn")
    ):
        # Another judge's assignment looks exactly like a missing one.
        raise not_found("No such assignment.")
    return item


def current_review(item: Assignment) -> Review | None:
    version = item.project.canonical_version
    return item.reviews.filter(version=version).first() if version else None


def _validate_scores(event: Event, scores: dict[str, int], submitting: bool) -> None:
    criteria = {c.key: c for c in event.criteria.all()}
    unknown = sorted(set(scores) - set(criteria))
    if unknown:
        raise unprocessable("Unknown criteria.", {"criteria": unknown})
    for key, value in scores.items():
        criterion = criteria[key]
        if not isinstance(value, int) or not criterion.min_score <= value <= criterion.max_score:
            raise unprocessable(
                f"'{criterion.label}' must be a whole number from {criterion.min_score} "
                f"to {criterion.max_score}.",
                {"criterion": key},
            )
    if submitting:
        missing = [c.label for c in criteria.values() if not c.is_bonus and c.key not in scores]
        if missing:
            raise unprocessable("Score every criterion before submitting.", {"missing": missing})


@transaction.atomic
def save_review(
    principal: Principal,
    item: Assignment,
    scores: dict[str, int],
    comment: str = "",
    improvement: str = "",
    submit: bool = False,
    active_seconds: int = 0,
) -> Review:
    event = item.event
    if event.phase != Phase.JUDGING:
        raise conflict("judging_closed", "Reviews can only be written while judging is open.")
    version = item.project.canonical_version
    if version is None:
        raise conflict("nothing_to_review", "This project has no submitted version.")
    _validate_scores(event, scores, submit)
    if submit and not comment.strip():
        raise unprocessable("Write feedback for the team before submitting.")
    review, _ = Review.objects.get_or_create(
        assignment=item,
        version=version,
        defaults={"event": event, "judge_id": item.judge_id, "project": item.project},
    )
    was_submitted = review.status == "submitted"
    review.comment = comment
    review.improvement = improvement
    review.active_seconds = review.active_seconds + max(0, min(active_seconds, 3600))
    if submit:
        review.status = "submitted"
        review.submitted_at = clock.now()
    review.save()
    for key, value in scores.items():
        criterion = event.criteria.get(key=key)
        ScoreItem.objects.update_or_create(
            review=review, criterion=criterion, defaults={"value": value}
        )
    if submit:
        Assignment.objects.filter(pk=item.pk).update(status="done")
        ledger.append(review, "amended" if was_submitted else "submitted")
        action = "review.amended" if was_submitted else "review.submitted"
        audit.record(
            action,
            f"{'Amended' if was_submitted else 'Submitted'} review of {item.project_id} "
            f"v{version.n}",
            actor=principal,
            actor_role="judge",
            event_id=event.pk,
            target=review,
        )
    return review


# --- Progress -----------------------------------------------------------------


def progress(event: Event) -> dict[str, Any]:
    """Who has not started, who is in progress, who is done, and coverage per project."""
    now = clock.now()
    overdue = bool(event.judging_closes_at and now > event.judging_closes_at)
    judges = {u.pk: u for u in User.objects.filter(pk__in=judge_ids(event))}
    rows = []
    for judge_id, user in sorted(judges.items(), key=lambda kv: kv[1].email):
        items = Assignment.objects.filter(
            event=event, judge_id=judge_id, status__in=["active", "done"]
        )
        assigned = items.count()
        submitted = (
            Review.objects.filter(event=event, judge_id=judge_id, status="submitted")
            .values("project_id")
            .distinct()
            .count()
        )
        drafts = Review.objects.filter(event=event, judge_id=judge_id, status="draft").count()
        last = Review.objects.filter(event=event, judge_id=judge_id).aggregate(
            last=Max("submitted_at")
        )["last"]
        if assigned == 0:
            state = "unassigned"
        elif submitted >= assigned:
            state = "done"
        elif submitted == 0 and drafts == 0:
            state = "overdue" if overdue else "not_started"
        else:
            state = "overdue" if overdue else "in_progress"
        rows.append(
            {
                "judge_id": judge_id,
                "email": user.email,
                "name": user.name,
                "assigned": assigned,
                "submitted": min(submitted, assigned) if assigned else submitted,
                "drafts": drafts,
                "state": state,
                "last_activity": last,
            }
        )
    coverage = []
    for project in Project.objects.filter(event=event, status="submitted").select_related(
        "canonical_version"
    ):
        reviewed = (
            Review.objects.filter(project=project, status="submitted")
            .values("judge_id")
            .distinct()
            .count()
        )
        coverage.append(
            {
                "project_id": project.pk,
                "project": project.canonical_version.name if project.canonical_version else "",
                "reviews": reviewed,
                "needed": event.reviews_per_project,
            }
        )
    totals = {
        state: sum(1 for r in rows if r["state"] == state)
        for state in ("not_started", "in_progress", "done", "overdue", "unassigned")
    }
    return {"judges": rows, "coverage": coverage, "totals": totals}
