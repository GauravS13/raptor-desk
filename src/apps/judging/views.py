"""Judge console pages and the organizer's judging page."""

from typing import Any

from django import forms
from django.contrib import messages
from django.http import HttpRequest, HttpResponse, HttpResponseRedirect
from django.shortcuts import render
from django.views.decorators.http import require_GET, require_http_methods, require_POST

from apps.events import policies as event_policies
from apps.events.services import get_event
from apps.judging import exports, services
from apps.judging.api import JUDGE_SELF
from apps.judging.models import Assignment
from apps.submissions.models import Project
from core.actions import ui_action
from core.http import ApiError, not_found
from core.policy import get_principal, policy


@require_GET
@policy(JUDGE_SELF)
def judge_home(request: HttpRequest) -> HttpResponse:
    items = services.queue(get_principal(request))
    events: dict[str, dict[str, Any]] = {}
    for item in items:
        group = events.setdefault(item.event_id, {"event": item.event, "items": [], "done": 0})
        group["items"].append(item)
        group["done"] += item.status == "done"
    return render(request, "judging/queue.html", {"groups": list(events.values())})


@require_http_methods(["GET", "POST"])
@ui_action("review.save")
@policy(JUDGE_SELF)
def review_page(request: HttpRequest, assignment_id: str) -> HttpResponse:
    principal = get_principal(request)
    item = services.own_assignment(principal, assignment_id)
    criteria = list(item.event.criteria.all())
    review = services.current_review(item)
    status = 200
    error = None
    if request.method == "POST":
        scores = {}
        for criterion in criteria:
            raw = request.POST.get(f"c_{criterion.key}", "").strip()
            if raw:
                try:
                    scores[criterion.key] = int(raw)
                except ValueError:
                    scores[criterion.key] = -1  # rejected by range validation
        try:
            services.save_review(
                principal,
                item,
                scores,
                request.POST.get("comment", ""),
                request.POST.get("improvement", ""),
                submit="submit" in request.POST,
                active_seconds=int(request.POST.get("active_seconds") or 0),
            )
        except ApiError as exc:
            missing = exc.details.get("missing")
            error = f"{exc.message} {', '.join(missing)}" if missing else exc.message
            status = exc.status
        else:
            if "submit" in request.POST:
                messages.success(request, "Review submitted.")
                return HttpResponseRedirect(_next_in_queue(principal, item))
            messages.success(request, "Draft saved.")
            return HttpResponseRedirect(f"/judge/review/{item.pk}")
        review = services.current_review(item)
    current = (
        {s.criterion.key: s.value for s in review.scores.select_related("criterion")}
        if review
        else {}
    )
    rows = [
        {
            "criterion": c,
            "levels": list(range(c.min_score, c.max_score + 1)),
            "value": current.get(c.key),
            "anchors": sorted(c.anchors.items(), key=lambda kv: int(kv[0])),
        }
        for c in criteria
    ]
    context = {
        "item": item,
        "version": item.project.canonical_version,
        "blind": item.event.blind_judging,
        "rows": rows,
        "review": review,
        "error": error,
        "open": item.event.phase == "judging",
    }
    return render(request, "judging/review.html", context, status=status)


def _next_in_queue(principal: Any, current: Assignment) -> str:
    for item in services.queue(principal, current.event_id):
        if item.pk != current.pk and item.status == "active":
            return f"/judge/review/{item.pk}"
    return "/judge"


class BatchForm(forms.Form):
    judge_id = forms.ChoiceField(label="Judge")
    project_ids = forms.MultipleChoiceField(label="Projects", widget=forms.SelectMultiple)


class ConflictForm(forms.Form):
    judge_id = forms.ChoiceField(label="Judge")
    team_id = forms.ChoiceField(label="Team")
    reason = forms.CharField(max_length=300, required=False)


def _choices(
    event_id: str,
) -> tuple[list[tuple[str, str]], list[tuple[str, str]], list[tuple[str, str]]]:
    event = get_event(event_id)
    judges = [
        (g.user_id, f"{g.user.name or g.user.email} ({g.user_id})")
        for g in event.grants.filter(role="judge").select_related("user").order_by("user__email")
    ]
    projects = [
        (p.pk, f"{p.canonical_version.name if p.canonical_version else p.pk} ({p.pk})")
        for p in Project.objects.filter(event=event, status="submitted").select_related(
            "canonical_version"
        )
    ]
    teams = [(t.pk, t.name) for t in event.teams.all()]
    return judges, projects, teams


@require_GET
@policy(event_policies.EVENTS_MANAGE)
def organizer_judging(request: HttpRequest, event_id: str) -> HttpResponse:
    event = get_event(event_id)
    judges, projects, teams = _choices(event_id)
    batch = BatchForm()
    batch.fields["judge_id"].choices = judges
    batch.fields["project_ids"].choices = projects
    conflict_form = ConflictForm()
    conflict_form.fields["judge_id"].choices = judges
    conflict_form.fields["team_id"].choices = teams
    context = {
        "event": event,
        "tab": "judging",
        "progress": services.progress(event),
        "proposed": event.assignments.filter(status="proposed").select_related("judge", "project"),
        "batch_form": batch,
        "conflict_form": conflict_form,
    }
    return render(request, "judging/organizer.html", context)


def _back(event_id: str) -> HttpResponse:
    return HttpResponseRedirect(f"/o/events/{event_id}/judging")


@require_POST
@ui_action("assignment.plan")
@policy(event_policies.EVENTS_MANAGE)
def organizer_plan(request: HttpRequest, event_id: str) -> HttpResponse:
    result = services.propose_assignments(get_principal(request), get_event(event_id))
    short = sum(result.shortfalls.values())
    messages.success(
        request,
        f"Proposed {len(result.proposed)} assignments."
        + (f" {short} review(s) could not be placed within tracks and load." if short else ""),
    )
    return _back(event_id)


@require_POST
@ui_action("assignment.publish")
@policy(event_policies.EVENTS_MANAGE)
def organizer_publish(request: HttpRequest, event_id: str) -> HttpResponse:
    count = services.publish_proposals(get_principal(request), get_event(event_id))
    messages.success(request, f"Published {count} assignments; judges have been emailed.")
    return _back(event_id)


@require_POST
@ui_action("assignment.batch")
@policy(event_policies.EVENTS_MANAGE)
def organizer_assign(request: HttpRequest, event_id: str) -> HttpResponse:
    judges, projects, _ = _choices(event_id)
    form = BatchForm(request.POST)
    form.fields["judge_id"].choices = judges
    form.fields["project_ids"].choices = projects
    if form.is_valid():
        try:
            items = services.assign_batch(
                get_principal(request),
                get_event(event_id),
                form.cleaned_data["judge_id"],
                form.cleaned_data["project_ids"],
            )
            messages.success(request, f"Assigned {len(items)} project(s).")
        except ApiError as exc:
            messages.error(request, exc.message)
    else:
        messages.error(request, "Choose a judge and at least one project.")
    return _back(event_id)


@require_POST
@ui_action("judging.declare_conflict")
@policy(event_policies.EVENTS_MANAGE)
def organizer_conflict(request: HttpRequest, event_id: str) -> HttpResponse:
    judges, _, teams = _choices(event_id)
    form = ConflictForm(request.POST)
    form.fields["judge_id"].choices = judges
    form.fields["team_id"].choices = teams
    if form.is_valid():
        try:
            services.declare_conflict(
                get_principal(request),
                get_event(event_id),
                form.cleaned_data["judge_id"],
                form.cleaned_data["team_id"],
                form.cleaned_data["reason"],
            )
            messages.success(request, "Conflict recorded; affected assignments were withdrawn.")
        except ApiError as exc:
            messages.error(request, exc.message)
    return _back(event_id)


@require_GET
@policy(event_policies.EVENTS_MANAGE)
def organizer_exports(request: HttpRequest, event_id: str) -> HttpResponse:
    event = get_event(event_id)
    context = {"event": event, "tab": "exports", "kinds": sorted(exports.EXPORTS)}
    return render(request, "judging/exports.html", context)


@require_GET
@policy(event_policies.EVENTS_MANAGE)
def organizer_export_csv(request: HttpRequest, event_id: str, kind: str) -> HttpResponse:
    export = exports.EXPORTS.get(kind)
    if export is None:
        raise not_found("Unknown export.")
    event = get_event(event_id)
    response = HttpResponse(export(event), content_type="text/csv; charset=utf-8")
    response["Content-Disposition"] = f'attachment; filename="{event.slug}-{kind}.csv"'
    return response
