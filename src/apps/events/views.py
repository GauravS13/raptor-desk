"""Organizer console pages. They call the same services as the REST API."""

from decimal import Decimal
from typing import Any

from django.contrib import messages
from django.http import HttpRequest, HttpResponse, HttpResponseRedirect
from django.shortcuts import render
from django.views.decorators.http import require_GET, require_http_methods, require_POST

from apps.events import forms, policies, services
from apps.events.models import PHASE_ORDER, Event, Phase
from apps.events.services import TRANSITIONS, CriterionSpec
from apps.events.templates_catalog import apply_template, catalog
from core.actions import ui_action
from core.http import ApiError
from core.policy import Rule, define, get_principal, policy

ORG_HOME = define(
    "org.home", Rule(authenticated=True, description="Signed-in users see events they organize.")
)


def _templates() -> list[tuple[str, str]]:
    return [(t.key, t.name) for t in catalog().values()]


def _error_message(exc: ApiError) -> str:
    problems = exc.details.get("problems")
    return f"{exc.message} {' '.join(problems)}" if problems else exc.message


def _base_context(event: Event, tab: str) -> dict[str, Any]:
    return {"event": event, "tab": tab, "phases": PHASE_ORDER}


@require_GET
@policy(ORG_HOME)
def org_home(request: HttpRequest) -> HttpResponse:
    principal = get_principal(request)
    events = (
        Event.objects.all()
        if principal.is_admin
        else Event.objects.filter(pk__in=principal.events_with_role("organizer"))
    )
    can_create = principal.is_admin or bool(principal.events_with_role("organizer"))
    return render(request, "events/org_home.html", {"events": events, "can_create": can_create})


@require_http_methods(["GET", "POST"])
@ui_action("event.create")
@policy(policies.EVENTS_CREATE)
def event_create(request: HttpRequest) -> HttpResponse:
    form = forms.EventCreateForm(request.POST or None, templates=_templates())
    if request.method == "POST" and form.is_valid():
        data = form.cleaned_data
        principal = get_principal(request)
        try:
            fields = {}
            if data["submissions_close_at"]:
                fields["submissions_close_at"] = data["submissions_close_at"]
            event = services.create_event(principal, data["name"], **fields)
            if data["template"]:
                apply_template(principal, event, data["template"])
        except ApiError as exc:
            form.add_error(None, _error_message(exc))
        else:
            messages.success(request, f"Created {event.name}.")
            return HttpResponseRedirect(f"/o/events/{event.pk}")
    return render(request, "events/event_create.html", {"form": form})


@require_GET
@policy(policies.EVENTS_MANAGE)
def event_overview(request: HttpRequest, event_id: str) -> HttpResponse:
    event = services.get_event(event_id)
    next_phases = sorted(TRANSITIONS.get(event.phase, set()), key=PHASE_ORDER.index)
    preflights = {phase: services.preflight_for(event, phase) for phase in next_phases}
    context = _base_context(event, "overview") | {
        "next_phases": [(phase, Phase(phase).label, preflights[phase]) for phase in next_phases],
        "transitions": event.transitions.all()[:10],
        "rubric_problems": services.rubric_problems(event),
    }
    return render(request, "events/event_overview.html", context)


@require_POST
@ui_action("event.transition")
@policy(policies.EVENTS_MANAGE)
def event_phase(request: HttpRequest, event_id: str) -> HttpResponse:
    event = services.get_event(event_id)
    form = forms.PhaseForm(request.POST)
    if form.is_valid():
        data = form.cleaned_data
        try:
            services.transition(
                get_principal(request), event, data["to"], data["reason"], data["override"]
            )
            messages.success(request, f"Moved to {Phase(data['to']).label}.")
        except ApiError as exc:
            messages.error(request, _error_message(exc))
    return HttpResponseRedirect(f"/o/events/{event.pk}")


@require_http_methods(["GET", "POST"])
@ui_action("event.update")
@policy(policies.EVENTS_MANAGE)
def event_settings(request: HttpRequest, event_id: str) -> HttpResponse:
    event = services.get_event(event_id)
    initial = {name: getattr(event, name) for name in forms.EventSettingsForm.base_fields}
    form = forms.EventSettingsForm(request.POST or None, initial=initial)
    if request.method == "POST" and form.is_valid():
        try:
            services.update_event(get_principal(request), event, **form.cleaned_data)
        except ApiError as exc:
            form.add_error(None, _error_message(exc))
        else:
            messages.success(request, "Settings saved.")
            return HttpResponseRedirect(f"/o/events/{event.pk}/settings")
    return render(
        request, "events/event_settings.html", _base_context(event, "settings") | {"form": form}
    )


def _rubric_initial(event: Event) -> list[dict[str, Any]]:
    return [spec.__dict__ for spec in services.specs_of(event)]


@require_http_methods(["GET", "POST"])
@ui_action("event.set_rubric")
@policy(policies.EVENTS_MANAGE)
def event_rubric(request: HttpRequest, event_id: str) -> HttpResponse:
    event = services.get_event(event_id)
    meta = forms.RubricMetaForm(request.POST or None, initial={"weighting": event.weighting})
    formset = forms.CriterionFormSet(request.POST or None, initial=_rubric_initial(event))
    status = 200
    if request.method == "POST" and meta.is_valid() and formset.is_valid():
        specs = [
            CriterionSpec(
                key=row["key"],
                label=row["label"] or row["key"],
                weight=row["weight"] if row["weight"] is not None else Decimal("0"),
                min_score=row["min_score"] if row["min_score"] is not None else 1,
                max_score=row["max_score"] if row["max_score"] is not None else 5,
                description=row["description"],
                is_gate=row["is_gate"],
                gate_threshold=row["gate_threshold"],
                is_bonus=row["is_bonus"],
            )
            for row in formset.cleaned_data
            if row and row.get("key")
        ]
        try:
            services.set_rubric(
                get_principal(request), event, meta.cleaned_data["weighting"], specs
            )
        except ApiError as exc:
            messages.error(request, _error_message(exc))
            status = exc.status
        else:
            messages.success(request, "Rubric saved.")
            return HttpResponseRedirect(f"/o/events/{event.pk}/rubric")
    context = _base_context(event, "rubric") | {
        "meta": meta,
        "formset": formset,
        "template_form": forms.TemplateForm(templates=_templates()),
        "locked": event.phase not in services.RUBRIC_EDITABLE,
    }
    return render(request, "events/event_rubric.html", context, status=status)


@require_POST
@ui_action("event.apply_template")
@policy(policies.EVENTS_MANAGE)
def event_apply_template(request: HttpRequest, event_id: str) -> HttpResponse:
    event = services.get_event(event_id)
    form = forms.TemplateForm(request.POST, templates=_templates())
    if form.is_valid():
        try:
            apply_template(get_principal(request), event, form.cleaned_data["key"])
            messages.success(request, "Template applied.")
        except ApiError as exc:
            messages.error(request, _error_message(exc))
    return HttpResponseRedirect(f"/o/events/{event.pk}/rubric")


@require_GET
@policy(policies.EVENTS_MANAGE)
def event_structure(request: HttpRequest, event_id: str) -> HttpResponse:
    event = services.get_event(event_id)
    context = _base_context(event, "structure") | {
        "track_form": forms.TrackForm(),
        "prize_form": forms.PrizeForm(initial={"rank": event.prizes.count() + 1}),
        "question_form": forms.QuestionForm(),
    }
    return render(request, "events/event_structure.html", context)


def _run(request: HttpRequest, event: Event, form: Any, action: Any, success: str) -> HttpResponse:
    if form.is_valid():
        try:
            action(form.cleaned_data)
            messages.success(request, success)
        except ApiError as exc:
            messages.error(request, _error_message(exc))
    else:
        messages.error(request, "Please correct the highlighted fields.")
    return HttpResponseRedirect(f"/o/events/{event.pk}/structure")


@require_POST
@ui_action("event.add_track")
@policy(policies.EVENTS_MANAGE)
def event_add_track(request: HttpRequest, event_id: str) -> HttpResponse:
    event = services.get_event(event_id)
    principal = get_principal(request)
    return _run(
        request,
        event,
        forms.TrackForm(request.POST),
        lambda d: services.add_track(principal, event, d["name"], d["description"]),
        "Track added.",
    )


@require_POST
@ui_action("event.add_prize")
@policy(policies.EVENTS_MANAGE)
def event_add_prize(request: HttpRequest, event_id: str) -> HttpResponse:
    event = services.get_event(event_id)
    principal = get_principal(request)
    return _run(
        request,
        event,
        forms.PrizeForm(request.POST),
        lambda d: services.add_prize(principal, event, d["name"], d["rank"], d["amount"]),
        "Prize added.",
    )


@require_POST
@ui_action("event.add_question")
@policy(policies.EVENTS_MANAGE)
def event_add_question(request: HttpRequest, event_id: str) -> HttpResponse:
    event = services.get_event(event_id)
    principal = get_principal(request)

    def add(data: dict[str, Any]) -> None:
        choices = [line.strip() for line in data["choices"].splitlines() if line.strip()]
        services.add_question(
            principal,
            event,
            data["label"],
            kind=data["kind"],
            required=data["required"],
            choices=choices,
        )

    return _run(request, event, forms.QuestionForm(request.POST), add, "Question added.")


@require_http_methods(["GET", "POST"])
@ui_action("event.grant_role")
@policy(policies.EVENTS_MANAGE)
def event_people(request: HttpRequest, event_id: str) -> HttpResponse:
    event = services.get_event(event_id)
    form = forms.GrantForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        data = form.cleaned_data
        try:
            services.grant_role(
                get_principal(request), event, data["email"], data["role"], data["name"]
            )
            messages.success(request, f"{data['email']} is now a {data['role']}.")
            return HttpResponseRedirect(f"/o/events/{event.pk}/people")
        except ApiError as exc:
            form.add_error(None, _error_message(exc))
    grants = event.grants.select_related("user").order_by("role", "user__email")
    context = _base_context(event, "people") | {"form": form, "grants": grants}
    return render(request, "events/event_people.html", context)


@require_POST
@ui_action("event.revoke_role")
@policy(policies.EVENTS_MANAGE)
def event_revoke(request: HttpRequest, event_id: str, user_id: str, role: str) -> HttpResponse:
    event = services.get_event(event_id)
    try:
        services.revoke_role(get_principal(request), event, user_id, role)
        messages.success(request, "Role removed.")
    except ApiError as exc:
        messages.error(request, _error_message(exc))
    return HttpResponseRedirect(f"/o/events/{event.pk}/people")
