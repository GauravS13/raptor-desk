"""Public event pages, the gallery, project pages and the team's submission form."""

from typing import Any

from django.contrib import messages
from django.core.paginator import Paginator
from django.http import HttpRequest, HttpResponse, HttpResponseRedirect
from django.shortcuts import render
from django.views.decorators.http import require_GET, require_http_methods, require_POST

from apps.events.api import visible_event
from apps.events.services import specs_of
from apps.submissions import services
from apps.submissions.api import GALLERY, PROJECT_VIEW, TEAM_SELF, readable_project
from apps.submissions.forms import SubmissionForm
from apps.submissions.services import SubmissionContent
from apps.teams.services import team_of
from core.actions import ui_action
from core.http import ApiError
from core.policy import Rule, define, get_principal, policy

EVENT_PAGE = define("events.page", Rule(public=True, description="Event pages are public."))

GALLERY_PAGE_SIZE = 60


@require_GET
@policy(EVENT_PAGE)
def event_page(request: HttpRequest, event_id: str) -> HttpResponse:
    event = visible_event(request, event_id)
    principal = get_principal(request)
    context = {
        "event": event,
        "criteria": specs_of(event),
        "my_team": team_of(principal, event),
        "project_count": len(services.gallery(event)),
    }
    return render(request, "submissions/event_page.html", context)


@require_GET
@policy(GALLERY)
def gallery_page(request: HttpRequest, event_id: str) -> HttpResponse:
    event = visible_event(request, event_id)
    query = request.GET.get("q", "").strip()
    track = request.GET.get("track", "").strip()
    tag = request.GET.get("tag", "").strip()
    projects = services.gallery(event, query, track, tag)
    page = Paginator(projects, GALLERY_PAGE_SIZE).get_page(request.GET.get("page"))
    context = {
        "event": event,
        "page": page,
        "query": query,
        "track": track,
        "tag": tag,
        "total": len(projects),
        "tracks": event.tracks.all(),
    }
    return render(request, "submissions/gallery.html", context)


@require_GET
@policy(PROJECT_VIEW)
def project_page(request: HttpRequest, project_id: str) -> HttpResponse:
    project = readable_project(request, project_id)
    principal = get_principal(request)
    version = project.canonical_version
    context = {
        "project": project,
        "event": project.event,
        "version": version,
        "answers": version.answers.select_related("question") if version else [],
        "is_member": services.is_member(principal, project),
        "draft": services.draft_of(project),
    }
    return render(request, "submissions/project.html", context)


def _initial(request: HttpRequest, event: Any) -> tuple[Any, dict[str, Any]]:
    team = services.team_for(get_principal(request), event)
    project = team.projects.filter(event=event).exclude(status="withdrawn").first()
    if project is None:
        return None, {}
    version = services.draft_of(project) or services.latest_version(project)
    initial = services.content_of(version)
    initial["tech_tags"] = ", ".join(initial.get("tech_tags", []))
    initial["track"] = project.track_id or ""
    if version is not None:
        for answer in version.answers.all():
            initial[f"q_{answer.question_id}"] = answer.value
    return project, initial


@require_http_methods(["GET", "POST"])
@ui_action("submission.save_draft")
@policy(TEAM_SELF)
def submit_page(request: HttpRequest, event_id: str) -> HttpResponse:
    event = visible_event(request, event_id)
    principal = get_principal(request)
    project, initial = _initial(request, event)
    form = SubmissionForm(request.POST or None, initial=initial, event=event)
    status = 200
    if request.method == "POST" and form.is_valid():
        data = form.cleaned_data
        content = SubmissionContent(
            name=data["name"],
            tagline=data["tagline"],
            description=data["description"],
            repo_url=data["repo_url"],
            video_url=data["video_url"],
            live_url=data["live_url"],
            tech_tags=form.tags(),
            track_id=data.get("track") or None,
            answers=form.answers(),
        )
        try:
            project = services.save_draft(principal, event, content)
            if "submit" in request.POST:
                version = services.submit(principal, project)
                messages.success(request, f"Submitted version {version.n}.")
                return HttpResponseRedirect(f"/projects/{project.pk}")
            messages.success(request, "Draft saved.")
            return HttpResponseRedirect(f"/events/{event.pk}/submit")
        except ApiError as exc:
            missing = exc.details.get("missing")
            form.add_error(None, f"{exc.message} {', '.join(missing)}" if missing else exc.message)
            status = exc.status
    context = {
        "event": event,
        "form": form,
        "project": project,
        "open": event.submissions_are_open,
        "draft": services.draft_of(project) if project else None,
    }
    return render(request, "submissions/submit.html", context, status=status)


@require_POST
@ui_action("submission.withdraw")
@policy(TEAM_SELF)
def withdraw(request: HttpRequest, project_id: str) -> HttpResponse:
    project = services.get_project(project_id)
    try:
        services.withdraw(get_principal(request), project)
        messages.success(request, "Project withdrawn.")
    except ApiError as exc:
        messages.error(request, exc.message)
    return HttpResponseRedirect(f"/events/{project.event_id}")


# The submit button on the submission form posts to submit_page; this marker
# records that the page offers the same action as POST /api/projects/{id}/submit.
ui_action("submission.submit")(submit_page)
ui_action("submission.edit_draft")(submit_page)
