"""Judge console pages and the organizer's judging page."""

import json
from typing import Any

from django import forms
from django.contrib import messages
from django.http import HttpRequest, HttpResponse, HttpResponseRedirect, JsonResponse
from django.shortcuts import render
from django.views.decorators.http import require_GET, require_http_methods, require_POST

from apps.accounts.models import User
from apps.events import policies as event_policies
from apps.events.services import get_event
from apps.judging import (
    close_calls,
    credentials,
    deliberation,
    exports,
    feedback,
    ledger,
    passport,
    publishing,
    services,
    snapshots,
)
from apps.judging import results as results_service
from apps.judging.api import FEEDBACK, JUDGE_SELF, PUBLIC_VERIFY, SIGNED_IN
from apps.judging.models import Assignment
from apps.submissions import services as submission_services
from apps.submissions.models import Project
from apps.voting import services as voting_services
from core.actions import ui_action
from core.http import ApiError, forbidden, not_found
from core.policy import Rule, define, get_principal, policy


@require_GET
@policy(JUDGE_SELF)
def judge_home(request: HttpRequest) -> HttpResponse:
    items = services.queue(get_principal(request))
    events: dict[str, dict[str, Any]] = {}
    for item in items:
        group = events.setdefault(item.event_id, {"event": item.event, "items": [], "done": 0})
        group["items"].append(item)
        group["done"] += item.status == "done"
    principal = get_principal(request)
    documents = credentials.Credential.objects.filter(user_id=principal.user_id)
    context = {
        "groups": list(events.values()),
        "documents": documents.select_related("event"),
        "passport_public": passport.is_public(principal.user_id or ""),
        "judge_id": principal.user_id,
    }
    return render(request, "judging/queue.html", context)


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
        "ledger": ledger.verify(event),
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


@require_GET
@policy(event_policies.EVENTS_MANAGE)
def organizer_results(request: HttpRequest, event_id: str) -> HttpResponse:
    event = get_event(event_id)
    method = request.GET.get("method", "additive")
    computed = results_service.compute(event, method=method)
    rows = results_service.as_rows(computed)
    judges = sorted(computed.evaluation.judges, key=lambda j: j.severity)
    budget = _budget(request.GET.get("budget"))
    doubt = close_calls.report(event, budget=budget, results=computed)
    names = dict(User.objects.filter(pk__in=services.judge_ids(event)).values_list("pk", "name"))
    context = {
        "event": event,
        "tab": "results",
        "method": computed.method,
        "methods": [
            ("additive", "Additive judge-bias model"),
            ("shrunk_z", "Shrunken z-score"),
            ("raw", "Raw average"),
        ],
        "rows": rows,
        "cutoffs": computed.evaluation.cutoffs,
        "flags": computed.evaluation.flags,
        "params": computed.evaluation.params,
        "judges": judges,
        "gated_out": computed.gated_out,
        "doubt": doubt,
        "project_names": doubt.results.names,
        "judge_names": names,
        "can_ask": event.phase == "judging",
        "budgets": (2, 4, 6, 10, 20),
    }
    return render(request, "judging/results.html", context)


def _budget(raw: str | None) -> int:
    try:
        return min(max(int(raw or close_calls.DEFAULT_BUDGET), 1), 100)
    except ValueError:
        return close_calls.DEFAULT_BUDGET


@require_POST
@ui_action("judging.close_calls_ask")
@policy(event_policies.EVENTS_MANAGE)
def organizer_ask(request: HttpRequest, event_id: str) -> HttpResponse:
    budget = _budget(request.POST.get("budget"))
    created = close_calls.approve(get_principal(request), get_event(event_id), budget=budget)
    if created:
        judges = len({item.judge_id for item in created})
        messages.success(
            request,
            f"Asked {judges} judge(s) for {len(created)} extra review(s); they have been emailed. "
            "The chances update as the reviews come in.",
        )
    else:
        messages.info(request, "Nothing to ask: no close call has an eligible judge left.")
    return HttpResponseRedirect(f"/o/events/{event_id}/results?budget={budget}")


@require_GET
@policy(event_policies.EVENTS_MANAGE)
def organizer_deliberation(request: HttpRequest, event_id: str) -> HttpResponse:
    event = get_event(event_id)
    found = deliberation.board(event)
    context = {
        "event": event,
        "tab": "deliberation",
        "board": found,
        "cutoffs": found.results.evaluation.cutoffs,
        "names": {row.project_id: row.name for row in found.rows},
        "actors": {
            user.pk: user.name or user.email
            for user in User.objects.filter(pk__in={d.actor_id for d in found.decisions})
        },
        "has_bonus": event.criteria.filter(is_bonus=True).exists(),
        "can_decide": event.phase in deliberation.OPEN_PHASES,
        "can_freeze": event.phase in snapshots.FREEZE_PHASES,
        "snapshots": list(event.results_snapshots.order_by("-number")),
        "latest_is_current": _latest_is_current(event),
    }
    return render(request, "judging/deliberation.html", context)


@require_POST
@ui_action("judging.record_decision")
@policy(event_policies.EVENTS_MANAGE)
def organizer_decide(request: HttpRequest, event_id: str) -> HttpResponse:
    try:
        deliberation.record_decision(
            get_principal(request),
            get_event(event_id),
            kind=request.POST.get("kind", ""),
            project_id=request.POST.get("project_id", ""),
            other_id=request.POST.get("other_id") or None,
            rationale=request.POST.get("rationale", ""),
        )
    except ApiError as exc:
        if exc.status == 409:
            raise
        messages.error(request, str(exc))
    else:
        messages.success(request, "Decision recorded in the deliberation log and the audit trail.")
    return HttpResponseRedirect(f"/o/events/{event_id}/deliberation")


def _latest_is_current(event: Any) -> bool | None:
    latest = snapshots.latest(event)
    return None if latest is None else snapshots.is_current(event, latest)


@require_POST
@ui_action("judging.freeze_results")
@policy(event_policies.EVENTS_MANAGE)
def organizer_freeze(request: HttpRequest, event_id: str) -> HttpResponse:
    snapshot = snapshots.freeze(get_principal(request), get_event(event_id))
    messages.success(
        request,
        f"Results frozen as snapshot #{snapshot.number} and signed "
        f"(SHA-256 {snapshot.payload_hash[:12]}).",
    )
    return HttpResponseRedirect(f"/o/events/{event_id}/deliberation")


@require_GET
@policy(event_policies.EVENTS_MANAGE)
def organizer_snapshot(request: HttpRequest, event_id: str, number: int) -> HttpResponse:
    item = get_event(event_id).results_snapshots.filter(number=number).first()
    if item is None:
        raise not_found("No such snapshot.")
    response = JsonResponse(snapshots.signed_document(item), json_dumps_params={"indent": 2})
    filename = f"{event_id}-results-{number}.json"
    response["Content-Disposition"] = f'attachment; filename="{filename}"'
    return response


PUBLIC_RESULTS = define(
    "public.results",
    Rule(public=True, description="Published results, with the method card, for everyone."),
)
METHOD_NAMES = {
    "additive": "Additive judge-bias model",
    "shrunk_z": "Shrunken z-score",
    "raw": "Raw average",
}


@require_GET
@policy(PUBLIC_RESULTS)
def public_results(request: HttpRequest, event_id: str) -> HttpResponse:
    event = get_event(event_id)
    principal = get_principal(request)
    if event.voting_is_open and not voting_services.results_visible_to(principal, event):
        raise forbidden("Results stay hidden until the community vote closes.")
    publication = publishing.published(event)
    if publication is None:
        raise not_found("Results for this event have not been published.")
    payload = publication.snapshot.payload
    prizes = {prize.rank: prize for prize in event.prizes.all()}
    context = {
        "event": event,
        "publication": publication,
        "snapshot": publication.snapshot,
        "payload": payload,
        "method_name": METHOD_NAMES.get(payload["method"], payload["method"]),
        "prizes": prizes,
        "names": {row["project"]: row["name"] for row in payload["ranking"]},
    }
    return render(request, "judging/public_results.html", context)


@require_GET
@policy(FEEDBACK)
def feedback_page(request: HttpRequest, project_id: str) -> HttpResponse:
    principal = get_principal(request)
    found = feedback.report(principal, project_id)
    context = {
        "report": found,
        "event": found.event,
        "can_query": submission_services.is_member(principal, found.project),
        "record": credentials.Credential.objects.filter(
            kind="participation", event=found.event, user_id=principal.user_id
        ).first(),
    }
    return render(request, "judging/feedback.html", context)


@require_POST
@ui_action("judging.query_result")
@policy(FEEDBACK)
def feedback_query(request: HttpRequest, project_id: str) -> HttpResponse:
    try:
        feedback.submit_query(get_principal(request), project_id, request.POST.get("message", ""))
    except ApiError as exc:
        if exc.status == 404:
            raise
        messages.error(request, str(exc))
    else:
        messages.success(request, "Query sent to the organizers. You will get an email answer.")
    return HttpResponseRedirect(f"/projects/{project_id}/feedback")


@require_GET
@policy(event_policies.EVENTS_MANAGE)
def organizer_queries(request: HttpRequest, event_id: str) -> HttpResponse:
    event = get_event(event_id)
    items = list(event.queries.select_related("project__canonical_version", "author"))
    return render(
        request, "judging/queries.html", {"event": event, "tab": "queries", "queries": items}
    )


@require_POST
@ui_action("judging.answer_query")
@policy(event_policies.EVENTS_MANAGE)
def organizer_answer(request: HttpRequest, event_id: str, query_id: str) -> HttpResponse:
    try:
        feedback.answer_query(
            get_principal(request), get_event(event_id), query_id, request.POST.get("response", "")
        )
    except ApiError as exc:
        if exc.status == 404:
            raise
        messages.error(request, str(exc))
    else:
        messages.success(request, "Answer saved and emailed to the team.")
    return HttpResponseRedirect(f"/o/events/{event_id}/queries")


@require_GET
@policy(SIGNED_IN)
def protocol_page(request: HttpRequest, code: str) -> HttpResponse:
    item = credentials.protocol_for(get_principal(request), code)
    names = dict(
        Project.objects.filter(event=item.event).values_list("pk", "canonical_version__name")
    )
    context = {"item": item, "payload": item.payload, "names": names, "event": item.event}
    return render(request, "judging/protocol.html", context)


@require_GET
@policy(SIGNED_IN)
def protocol_json(request: HttpRequest, code: str) -> HttpResponse:
    item = credentials.protocol_for(get_principal(request), code)
    response = JsonResponse(credentials.signed_document(item), json_dumps_params={"indent": 2})
    response["Content-Disposition"] = f'attachment; filename="protocol-{item.label}.json"'
    return response


@require_GET
@policy(PUBLIC_VERIFY)
def verify_page(request: HttpRequest, code: str) -> HttpResponse:
    item = credentials.public_record(code)
    document = credentials.signed_document(item)
    context = {
        "item": item,
        "payload": item.payload,
        "valid": credentials.verify(document),
        "event": item.event,
    }
    return render(request, "judging/verify_record.html", context)


@require_POST
@ui_action("judging.passport")
@policy(JUDGE_SELF)
def passport_toggle(request: HttpRequest) -> HttpResponse:
    public = passport.set_public(get_principal(request), request.POST.get("public") == "1")
    messages.success(
        request,
        "Your judge passport is public." if public else "Your judge passport is private.",
    )
    return HttpResponseRedirect("/judge")


@require_GET
@policy(PUBLIC_VERIFY)
def passport_page(request: HttpRequest, judge_id: str) -> HttpResponse:
    user, items = passport.passport(judge_id)
    return render(request, "judging/passport.html", {"judge": user, "items": items})


MAX_DOCUMENT = 2_000_000


@require_http_methods(["GET", "POST"])
@policy(PUBLIC_VERIFY)
def verify_form(request: HttpRequest) -> HttpResponse:
    """Check a code, or a pasted signed document, without installing anything."""
    code = request.GET.get("code", "").strip()
    if code:
        return HttpResponseRedirect(f"/verify/{code}")
    result = None
    if request.method == "POST":
        text = request.POST.get("document", "")
        if len(text) > MAX_DOCUMENT:
            result = {"valid": "no", "reason": "the document is too large to check here"}
        else:
            try:
                result = credentials.describe(json.loads(text))
            except ValueError:
                result = {"valid": "no", "reason": "that is not JSON"}
    return render(request, "judging/verify_form.html", {"result": result})
