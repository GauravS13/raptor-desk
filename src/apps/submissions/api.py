"""REST API for submissions and the public gallery.

``POST /api/events/{event_id}/submissions`` accepts ``title``/``summary`` as
aliases for ``name``/``tagline``, so simple clients (including the official
acceptance checker) can post a minimal body.
"""

from datetime import datetime

from django.http import HttpRequest
from ninja import Router, Schema, Status

from apps.events.api import visible_event
from apps.submissions import services
from apps.submissions.models import Project, ProjectVersion
from apps.submissions.services import SubmissionContent
from core.actions import api_action
from core.http import not_found
from core.policy import Principal, Rule, define, get_principal, policy

GALLERY = define(
    "submissions.gallery", Rule(public=True, description="The project gallery is public.")
)
PROJECT_VIEW = define(
    "submissions.view",
    Rule(public=True, description="Submitted projects are public; drafts only to their team."),
)
SUBMIT = define(
    "submissions.create",
    Rule(roles=frozenset({"participant"}), description="Participants of this event submit."),
)
TEAM_SELF = define(
    "submissions.team",
    Rule(authenticated=True, description="Team members edit their own project."),
)

router = Router(tags=["submissions"])


class SubmissionIn(Schema):
    name: str = ""
    title: str = ""
    tagline: str = ""
    summary: str = ""
    description: str = ""
    video_url: str = ""
    repo_url: str = ""
    live_url: str = ""
    tech_tags: list[str] = []
    track_id: str | None = None
    answers: dict[str, str] = {}
    submit: bool = False

    def content(self) -> SubmissionContent:
        return SubmissionContent(
            name=self.name or self.title,
            tagline=self.tagline or self.summary,
            description=self.description,
            video_url=self.video_url,
            repo_url=self.repo_url,
            live_url=self.live_url,
            tech_tags=self.tech_tags,
            track_id=self.track_id,
            answers=self.answers,
        )


class VersionOut(Schema):
    id: str
    n: int
    name: str
    tagline: str
    description: str
    video_url: str
    repo_url: str
    live_url: str
    tech_tags: list[str]
    submitted_at: datetime | None


class ProjectOut(Schema):
    id: str
    event_id: str
    team_id: str
    team_name: str
    track_id: str | None
    track_name: str | None
    status: str
    canonical: VersionOut | None
    draft: VersionOut | None = None


def version_out(version: ProjectVersion | None) -> VersionOut | None:
    if version is None:
        return None
    return VersionOut(
        id=version.pk,
        n=version.n,
        name=version.name,
        tagline=version.tagline,
        description=version.description,
        video_url=version.video_url,
        repo_url=version.repo_url,
        live_url=version.live_url,
        tech_tags=list(version.tech_tags or []),
        submitted_at=version.submitted_at,
    )


def project_out(project: Project, principal: Principal) -> ProjectOut:
    show_draft = services.is_member(principal, project)
    return ProjectOut(
        id=project.pk,
        event_id=project.event_id,
        team_id=project.team_id,
        team_name=project.team.name,
        track_id=project.track_id,
        track_name=project.track.name if project.track else None,
        status=project.status,
        canonical=version_out(project.canonical_version),
        draft=version_out(services.draft_of(project)) if show_draft else None,
    )


def readable_project(request: HttpRequest, project_id: str) -> Project:
    principal = get_principal(request)
    project = services.get_project(project_id)
    visible_event(request, project.event_id)
    allowed = (
        project.is_public
        or services.is_member(principal, project)
        or principal.is_admin
        or principal.has_role("organizer", project.event_id)
    )
    if not allowed:
        raise not_found("No such project.")
    return project


@router.get("/events/{event_id}/projects", response=list[ProjectOut])
@policy(GALLERY)
def list_projects(
    request: HttpRequest, event_id: str, q: str = "", track: str = "", tag: str = ""
) -> list[ProjectOut]:
    event = visible_event(request, event_id)
    principal = get_principal(request)
    return [project_out(p, principal) for p in services.gallery(event, q, track, tag)]


@router.post("/events/{event_id}/submissions", response={201: ProjectOut})
@api_action("submission.save_draft")
@policy(SUBMIT)
def create_submission(
    request: HttpRequest, event_id: str, payload: SubmissionIn
) -> Status[ProjectOut]:
    principal = get_principal(request)
    event = visible_event(request, event_id)
    services.require_open(event)  # deadline first: a late post is refused for being late
    project = services.save_draft(principal, event, payload.content(), source="api")
    if payload.submit:
        services.submit(principal, project)
        project.refresh_from_db()
    return Status(201, project_out(project, principal))


@router.get("/projects/{project_id}", response=ProjectOut)
@policy(PROJECT_VIEW)
def get_project(request: HttpRequest, project_id: str) -> ProjectOut:
    return project_out(readable_project(request, project_id), get_principal(request))


@router.put("/projects/{project_id}/draft", response=ProjectOut)
@api_action("submission.edit_draft")
@policy(TEAM_SELF)
def edit_draft(request: HttpRequest, project_id: str, payload: SubmissionIn) -> ProjectOut:
    principal = get_principal(request)
    project = services.get_project(project_id)
    services.assert_can_edit(principal, project)
    project = services.save_draft(principal, project.event, payload.content(), source="api")
    return project_out(project, principal)


@router.post("/projects/{project_id}/submit", response=ProjectOut)
@api_action("submission.submit")
@policy(TEAM_SELF)
def submit(request: HttpRequest, project_id: str) -> ProjectOut:
    principal = get_principal(request)
    project = services.get_project(project_id)
    services.submit(principal, project)
    project.refresh_from_db()
    return project_out(project, principal)


@router.post("/projects/{project_id}/withdraw", response=ProjectOut)
@api_action("submission.withdraw")
@policy(TEAM_SELF)
def withdraw(request: HttpRequest, project_id: str) -> ProjectOut:
    principal = get_principal(request)
    project = services.withdraw(principal, services.get_project(project_id))
    return project_out(project, principal)


@router.get("/projects/{project_id}/versions", response=list[VersionOut])
@policy(TEAM_SELF)
def list_versions(request: HttpRequest, project_id: str) -> list[VersionOut]:
    principal = get_principal(request)
    project = services.get_project(project_id)
    if not (
        services.is_member(principal, project)
        or principal.is_admin
        or principal.has_role("organizer", project.event_id)
    ):
        raise not_found("No such project.")
    return [version_out(v) for v in project.versions.order_by("n")]  # type: ignore[misc]
