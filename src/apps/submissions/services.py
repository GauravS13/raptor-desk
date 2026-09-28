"""Draft, submit, edit and withdraw projects. Shared by the submission pages and the REST API.

The deadline is checked first, before anything else, so a late submission is
always refused for the right reason: the event has closed.
"""

from dataclasses import dataclass, field
from typing import Any

from django.db import transaction
from django.db.models import Max

from apps.events.models import CustomQuestion, Event, Track
from apps.integrations import webhooks
from apps.submissions.models import CustomAnswer, Project, ProjectStatus, ProjectVersion
from apps.teams.models import Team, TeamMember
from core import audit, clock
from core.http import ApiError, conflict, forbidden, not_found, unprocessable
from core.policy import Principal

CONTENT_FIELDS = (
    "name",
    "tagline",
    "description",
    "video_url",
    "repo_url",
    "live_url",
    "tech_tags",
)


@dataclass
class SubmissionContent:
    name: str = ""
    tagline: str = ""
    description: str = ""
    video_url: str = ""
    repo_url: str = ""
    live_url: str = ""
    tech_tags: list[str] = field(default_factory=list)
    track_id: str | None = None
    answers: dict[str, str] = field(default_factory=dict)


def require_open(event: Event) -> None:
    if not event.submissions_are_open:
        close = event.submissions_close_at.isoformat() if event.submissions_close_at else None
        raise conflict(
            "submissions_closed",
            "Submissions for this event are closed.",
            {"submissions_close_at": close, "now": clock.now().isoformat()},
        )


def get_project(project_id: str) -> Project:
    project = (
        Project.objects.select_related("event", "team", "track", "canonical_version")
        .filter(pk=project_id)
        .first()
    )
    if project is None:
        raise not_found("No such project.")
    return project


def team_for(principal: Principal, event: Event) -> Team:
    membership = (
        TeamMember.objects.select_related("team")
        .filter(event=event, user_id=principal.user_id)
        .first()
    )
    if membership is None:
        raise forbidden("Join or create a team before submitting.")
    return membership.team


def is_member(principal: Principal, project: Project) -> bool:
    return (
        bool(principal.user_id)
        and TeamMember.objects.filter(team_id=project.team_id, user_id=principal.user_id).exists()
    )


def draft_of(project: Project) -> ProjectVersion | None:
    return project.versions.filter(submitted_at__isnull=True).order_by("-n").first()


def latest_version(project: Project) -> ProjectVersion | None:
    return project.versions.order_by("-n").first()


def _validate(event: Event, content: SubmissionContent) -> Track | None:
    if not content.name.strip():
        raise unprocessable("A project needs a name.")
    track = None
    if content.track_id:
        track = event.tracks.filter(pk=content.track_id).first()
        if track is None:
            raise unprocessable("That track does not belong to this event.")
    known = {q.pk for q in event.questions.all()}
    unknown = set(content.answers) - known
    if unknown:
        raise unprocessable("Answers refer to unknown questions.", {"questions": sorted(unknown)})
    return track


def _write_answers(version: ProjectVersion, answers: dict[str, str]) -> None:
    for question_id, value in answers.items():
        CustomAnswer.objects.update_or_create(
            version=version, question_id=question_id, defaults={"value": value}
        )


def _missing_required(event: Event, version: ProjectVersion) -> list[str]:
    answered = {a.question_id for a in version.answers.all() if a.value.strip()}
    required = CustomQuestion.objects.filter(event=event, required=True)
    return [q.label for q in required if q.pk not in answered]


@transaction.atomic
def save_draft(
    principal: Principal, event: Event, content: SubmissionContent, source: str = "ui"
) -> Project:
    """Create the team's project or update its working draft."""
    require_open(event)
    team = team_for(principal, event)
    track = _validate(event, content)
    project = team.projects.filter(event=event).exclude(status=ProjectStatus.WITHDRAWN).first()
    if project is None:
        project = Project.objects.create(
            event=event,
            team=team,
            track=track,
            gallery_order=(event.projects.aggregate(m=Max("gallery_order"))["m"] or 0) + 1,
        )
    elif track is not None:
        project.track = track
        project.save(update_fields=["track", "updated_at"])
    draft = draft_of(project)
    values = {name: getattr(content, name) for name in CONTENT_FIELDS}
    if draft is None:
        previous = latest_version(project)
        draft = ProjectVersion.objects.create(
            project=project, n=(previous.n + 1) if previous else 1, source=source, **values
        )
    else:
        ProjectVersion.objects.filter(pk=draft.pk).update(**values)
        draft.refresh_from_db()
    _write_answers(draft, content.answers)
    audit.record(
        "submission.draft_saved",
        f"Saved draft v{draft.n} of '{draft.name}'",
        actor=principal,
        actor_role="participant",
        event_id=event.pk,
        target=project,
    )
    return project


@transaction.atomic
def submit(principal: Principal, project: Project) -> ProjectVersion:
    """Freeze the working draft as the new canonical version."""
    require_open(project.event)
    if not is_member(principal, project):
        raise forbidden("Only the team can submit its project.")
    draft = draft_of(project)
    if draft is None:
        raise conflict("nothing_to_submit", "There is no unsubmitted draft to submit.")
    missing = _missing_required(project.event, draft)
    if missing:
        raise unprocessable("Answer the required questions first.", {"missing": missing})
    now = clock.now()
    ProjectVersion.objects.filter(pk=draft.pk).update(submitted_at=now)
    draft.refresh_from_db()
    project.canonical_version = draft
    project.status = ProjectStatus.SUBMITTED
    project.save(update_fields=["canonical_version", "status", "updated_at"])
    webhooks.emit(
        project.event_id,
        "project.submitted",
        {"project": project.pk, "version": draft.n, "name": draft.name},
    )
    audit.record(
        "submission.submitted",
        f"Submitted v{draft.n} of '{draft.name}'",
        actor=principal,
        actor_role="participant",
        event_id=project.event_id,
        target=project,
        details={"version": draft.n},
    )
    return draft


@transaction.atomic
def withdraw(principal: Principal, project: Project) -> Project:
    require_open(project.event)
    if not is_member(principal, project):
        raise forbidden("Only the team can withdraw its project.")
    project.status = ProjectStatus.WITHDRAWN
    project.save(update_fields=["status", "updated_at"])
    audit.record(
        "submission.withdrawn",
        "Project withdrawn",
        actor=principal,
        actor_role="participant",
        event_id=project.event_id,
        target=project,
    )
    return project


def content_of(version: ProjectVersion | None) -> dict[str, Any]:
    if version is None:
        return {}
    return {name: getattr(version, name) for name in CONTENT_FIELDS}


def assert_can_edit(principal: Principal, project: Project) -> None:
    if not is_member(principal, project):
        raise forbidden("Only the team can edit its project.")
    if project.status == ProjectStatus.WITHDRAWN:
        raise ApiError(409, "withdrawn", "This project was withdrawn.")


def gallery(event: Event, query: str = "", track_id: str = "", tag: str = "") -> list[Project]:
    """Public projects in a stable order (the order they were submitted or imported)."""
    projects = (
        Project.objects.select_related("team", "track", "canonical_version")
        .filter(event=event, status=ProjectStatus.SUBMITTED, canonical_version__isnull=False)
        .order_by("gallery_order", "created_at")
    )
    if query:
        from django.db.models import Q

        projects = projects.filter(
            Q(canonical_version__name__icontains=query)
            | Q(canonical_version__tagline__icontains=query)
            | Q(canonical_version__description__icontains=query)
            | Q(team__name__icontains=query)
        )
    if track_id:
        projects = projects.filter(track_id=track_id)
    result = list(projects)
    if tag:
        wanted = tag.strip().lower()
        result = [
            p for p in result if wanted in {t.lower() for t in p.canonical_version.tech_tags or []}
        ]
    return result
