"""Project comments: signed-in people only, rate-limited, always rendered escaped.

Organizers can hide a comment; it stays in the database and the audit trail.
"""

from django.db import transaction
from django.db.models import QuerySet

from apps.integrations import webhooks
from apps.submissions.models import Project
from apps.voting.models import Comment
from core import audit, clock, ratelimit
from core.http import not_found, unauthorized, unprocessable
from core.policy import Principal

MAX_LENGTH = 2000
PER_MINUTE = 10


def visible(project: Project) -> QuerySet[Comment]:
    return project.comments.filter(hidden_at__isnull=True).select_related("author")


def commentable(project_id: str) -> Project:
    project = (
        Project.objects.select_related("event").filter(pk=project_id, status="submitted").first()
    )
    if project is None:
        raise not_found("No such project.")
    return project


@transaction.atomic
def post(principal: Principal, project_id: str, body: str) -> Comment:
    if not principal.is_authenticated:
        raise unauthorized("Sign in to comment.")
    project = commentable(project_id)
    body = body.strip()
    if not body:
        raise unprocessable("Write something first.")
    if len(body) > MAX_LENGTH:
        raise unprocessable(f"Keep comments under {MAX_LENGTH} characters.")
    ratelimit.hit("comment", principal.user_id or "", limit=PER_MINUTE, window_seconds=60)
    comment = Comment.objects.create(project=project, author_id=principal.user_id, body=body)
    webhooks.emit(
        project.event_id,
        "comment.created",
        {"comment": comment.pk, "project": project.pk, "body": comment.body},
    )
    audit.record(
        "comment.created",
        f"Comment on {project.pk}",
        actor=principal,
        event_id=project.event_id,
        target=comment,
    )
    return comment


@transaction.atomic
def hide(actor: Principal, event_id: str, comment_id: str) -> Comment:
    comment = (
        Comment.objects.select_related("project")
        .filter(pk=comment_id, project__event_id=event_id)
        .first()
    )
    if comment is None:
        raise not_found("No such comment.")
    if comment.hidden_at is None:
        comment.hidden_at = clock.now()
        comment.hidden_by_id = actor.user_id or ""
        comment.save(update_fields=["hidden_at", "hidden_by_id"])
        audit.record(
            "comment.hidden",
            f"Hid a comment on {comment.project_id}",
            actor=actor,
            actor_role="organizer",
            event_id=event_id,
            target=comment,
        )
    return comment
