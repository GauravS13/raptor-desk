"""REST API for event configuration: events, rubric, tracks, prizes, questions, people, phases."""

from datetime import datetime
from decimal import Decimal
from typing import Any

from django.http import HttpRequest
from ninja import Router, Schema, Status

from apps.events import policies, services
from apps.events.models import Event, Phase
from apps.events.services import CriterionSpec
from apps.events.templates_catalog import apply_template, catalog
from core.actions import api_action
from core.http import not_found
from core.policy import Principal, get_principal, policy

router = Router(tags=["events"])


def can_see(principal: Principal, event: Event) -> bool:
    if event.phase != Phase.DRAFT:
        return True
    return principal.is_admin or principal.has_role("organizer", event.pk)


def visible_event(request: HttpRequest, event_id: str) -> Event:
    event = services.get_event(event_id)
    if not can_see(get_principal(request), event):
        raise not_found("No such event.")  # drafts are invisible, not merely forbidden
    return event


# --- Schemas ----------------------------------------------------------------


class EventOut(Schema):
    id: str
    slug: str
    name: str
    description: str
    phase: str
    template_key: str
    weighting: str
    registration_opens_at: datetime | None
    registration_closes_at: datetime | None
    submissions_open_at: datetime | None
    submissions_close_at: datetime | None
    judging_closes_at: datetime | None
    results_published_at: datetime | None
    reviews_per_project: int
    max_load_per_judge: int
    blind_judging: bool
    max_team_size: int
    allow_multiple_projects: bool
    voting_mode: str
    voting_scheme: str
    qv_credits: int
    voting_opens_at: datetime | None
    voting_closes_at: datetime | None
    submissions_are_open: bool


class EventSettings(Schema):
    description: str | None = None
    registration_opens_at: datetime | None = None
    registration_closes_at: datetime | None = None
    submissions_open_at: datetime | None = None
    submissions_close_at: datetime | None = None
    judging_closes_at: datetime | None = None
    reviews_per_project: int | None = None
    max_load_per_judge: int | None = None
    blind_judging: bool | None = None
    max_team_size: int | None = None
    allow_multiple_projects: bool | None = None
    voting_mode: str | None = None
    voting_scheme: str | None = None
    qv_credits: int | None = None
    voting_opens_at: datetime | None = None
    voting_closes_at: datetime | None = None


class EventCreate(EventSettings):
    name: str
    slug: str | None = None
    template: str | None = None


class EventPatch(EventSettings):
    name: str | None = None


class CriterionIO(Schema):
    key: str
    label: str
    weight: Decimal
    min_score: int = 1
    max_score: int = 5
    description: str = ""
    anchors: dict[str, str] = {}
    is_gate: bool = False
    gate_threshold: Decimal | None = None
    is_bonus: bool = False


class RubricIO(Schema):
    weighting: str
    criteria: list[CriterionIO]


class TrackIn(Schema):
    name: str
    description: str = ""


class TrackOut(Schema):
    id: str
    name: str
    description: str


class PrizeIn(Schema):
    name: str
    rank: int
    amount: str = ""
    track_id: str | None = None


class PrizeOut(Schema):
    id: str
    name: str
    rank: int
    amount: str
    track_id: str | None


class QuestionIn(Schema):
    label: str
    kind: str = "text"
    required: bool = False
    choices: list[str] = []
    help_text: str = ""


class QuestionOut(Schema):
    id: str
    label: str
    kind: str
    required: bool
    choices: list[str]
    help_text: str


class TemplateOut(Schema):
    key: str
    name: str
    description: str
    weighting: str


class TemplateIn(Schema):
    key: str


class GrantIn(Schema):
    email: str
    role: str
    name: str = ""


class GrantOut(Schema):
    user_id: str
    email: str
    name: str
    role: str


class CheckOut(Schema):
    level: str
    code: str
    message: str


class PhaseIn(Schema):
    to: str
    reason: str = ""
    override: bool = False


class TransitionOut(Schema):
    from_phase: str
    to_phase: str
    overridden: bool
    checks: list[CheckOut]


def event_out(event: Event) -> EventOut:
    data: dict[str, Any] = {name: getattr(event, name) for name in EventOut.model_fields}
    return EventOut(**data)


def _settings(payload: EventSettings, *, exclude: set[str] | None = None) -> dict[str, Any]:
    values = payload.model_dump(exclude_unset=True, exclude=exclude or set())
    return {key: value for key, value in values.items() if key in services.EDITABLE_FIELDS}


# --- Events -----------------------------------------------------------------


@router.get("", response=list[EventOut])
@policy(policies.EVENTS_LIST)
def list_events(request: HttpRequest) -> list[EventOut]:
    principal = get_principal(request)
    events = Event.objects.all()
    return [event_out(event) for event in events if can_see(principal, event)]


@router.post("", response={201: EventOut})
@api_action("event.create")
@policy(policies.EVENTS_CREATE)
def create_event(request: HttpRequest, payload: EventCreate) -> Status[EventOut]:
    principal = get_principal(request)
    event = services.create_event(
        principal, payload.name, payload.slug, **_settings(payload, exclude={"name", "slug"})
    )
    if payload.template:
        apply_template(principal, event, payload.template)
        event.refresh_from_db()
    return Status(201, event_out(event))


@router.get("/{event_id}", response=EventOut)
@policy(policies.EVENTS_VIEW)
def get_event(request: HttpRequest, event_id: str) -> EventOut:
    return event_out(visible_event(request, event_id))


@router.patch("/{event_id}", response=EventOut)
@api_action("event.update")
@policy(policies.EVENTS_MANAGE)
def update_event(request: HttpRequest, event_id: str, payload: EventPatch) -> EventOut:
    event = services.get_event(event_id)
    fields = _settings(payload)
    if payload.name is not None:
        fields["name"] = payload.name
    return event_out(services.update_event(get_principal(request), event, **fields))


@router.post("/{event_id}/template", response=EventOut)
@api_action("event.apply_template")
@policy(policies.EVENTS_MANAGE)
def use_template(request: HttpRequest, event_id: str, payload: TemplateIn) -> EventOut:
    event = services.get_event(event_id)
    apply_template(get_principal(request), event, payload.key)
    event.refresh_from_db()
    return event_out(event)


# --- Rubric -----------------------------------------------------------------


@router.get("/{event_id}/rubric", response=RubricIO)
@policy(policies.EVENTS_VIEW)
def get_rubric(request: HttpRequest, event_id: str) -> RubricIO:
    event = visible_event(request, event_id)
    return RubricIO(
        weighting=event.weighting,
        criteria=[CriterionIO(**spec.__dict__) for spec in services.specs_of(event)],
    )


@router.put("/{event_id}/rubric", response=RubricIO)
@api_action("event.set_rubric")
@policy(policies.EVENTS_MANAGE)
def put_rubric(request: HttpRequest, event_id: str, payload: RubricIO) -> RubricIO:
    event = services.get_event(event_id)
    specs = [CriterionSpec(**criterion.model_dump()) for criterion in payload.criteria]
    services.set_rubric(get_principal(request), event, payload.weighting, specs)
    return get_rubric(request, event_id=event_id)


# --- Tracks, prizes, questions ------------------------------------------------------


@router.get("/{event_id}/tracks", response=list[TrackOut])
@policy(policies.EVENTS_VIEW)
def list_tracks(request: HttpRequest, event_id: str) -> list[Any]:
    return list(visible_event(request, event_id).tracks.all())


@router.post("/{event_id}/tracks", response={201: TrackOut})
@api_action("event.add_track")
@policy(policies.EVENTS_MANAGE)
def add_track(request: HttpRequest, event_id: str, payload: TrackIn) -> Status[Any]:
    event = services.get_event(event_id)
    return Status(
        201, services.add_track(get_principal(request), event, payload.name, payload.description)
    )


@router.get("/{event_id}/prizes", response=list[PrizeOut])
@policy(policies.EVENTS_VIEW)
def list_prizes(request: HttpRequest, event_id: str) -> list[Any]:
    return list(visible_event(request, event_id).prizes.all())


@router.post("/{event_id}/prizes", response={201: PrizeOut})
@api_action("event.add_prize")
@policy(policies.EVENTS_MANAGE)
def add_prize(request: HttpRequest, event_id: str, payload: PrizeIn) -> Status[Any]:
    event = services.get_event(event_id)
    prize = services.add_prize(
        get_principal(request), event, payload.name, payload.rank, payload.amount, payload.track_id
    )
    return Status(201, prize)


@router.get("/{event_id}/questions", response=list[QuestionOut])
@policy(policies.EVENTS_VIEW)
def list_questions(request: HttpRequest, event_id: str) -> list[Any]:
    return list(visible_event(request, event_id).questions.all())


@router.post("/{event_id}/questions", response={201: QuestionOut})
@api_action("event.add_question")
@policy(policies.EVENTS_MANAGE)
def add_question(request: HttpRequest, event_id: str, payload: QuestionIn) -> Status[Any]:
    event = services.get_event(event_id)
    question = services.add_question(
        get_principal(request),
        event,
        payload.label,
        kind=payload.kind,
        required=payload.required,
        choices=payload.choices,
        help_text=payload.help_text,
    )
    return Status(201, question)


# --- People -----------------------------------------------------------------


@router.get("/{event_id}/people", response=list[GrantOut])
@policy(policies.EVENTS_MANAGE)
def list_people(request: HttpRequest, event_id: str) -> list[GrantOut]:
    event = services.get_event(event_id)
    return [
        GrantOut(user_id=g.user_id, email=g.user.email, name=g.user.name, role=g.role)
        for g in event.grants.select_related("user")
    ]


@router.post("/{event_id}/people", response={201: GrantOut})
@api_action("event.grant_role")
@policy(policies.EVENTS_MANAGE)
def grant_role(request: HttpRequest, event_id: str, payload: GrantIn) -> Status[GrantOut]:
    event = services.get_event(event_id)
    grant = services.grant_role(
        get_principal(request), event, payload.email, payload.role, payload.name
    )
    user = grant.user
    return Status(201, GrantOut(user_id=user.pk, email=user.email, name=user.name, role=grant.role))


@router.delete("/{event_id}/people/{user_id}/{role}", response={204: None})
@api_action("event.revoke_role")
@policy(policies.EVENTS_MANAGE)
def revoke_role(request: HttpRequest, event_id: str, user_id: str, role: str) -> Status[None]:
    services.revoke_role(get_principal(request), services.get_event(event_id), user_id, role)
    return Status(204, None)


# --- Lifecycle --------------------------------------------------------------


@router.get("/{event_id}/preflight", response=list[CheckOut])
@policy(policies.EVENTS_MANAGE)
def preflight(request: HttpRequest, event_id: str, to: str) -> list[CheckOut]:
    event = services.get_event(event_id)
    return [CheckOut(**check.as_dict()) for check in services.preflight_for(event, to)]


@router.post("/{event_id}/phase", response=TransitionOut)
@api_action("event.transition")
@policy(policies.EVENTS_MANAGE)
def change_phase(request: HttpRequest, event_id: str, payload: PhaseIn) -> TransitionOut:
    event = services.get_event(event_id)
    entry = services.transition(
        get_principal(request), event, payload.to, payload.reason, payload.override
    )
    return TransitionOut(
        from_phase=entry.from_phase,
        to_phase=entry.to_phase,
        overridden=entry.overridden,
        checks=[CheckOut(**check) for check in entry.preflight],
    )


# --- Templates --------------------------------------------------------------

templates_router = Router(tags=["events"])


@templates_router.get("", response=list[TemplateOut])
@policy(policies.TEMPLATES_LIST)
def list_templates(request: HttpRequest) -> list[TemplateOut]:
    return [
        TemplateOut(key=t.key, name=t.name, description=t.description, weighting=t.weighting)
        for t in catalog().values()
    ]
