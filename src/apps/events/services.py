"""Event configuration services: shared by organizer pages and the REST API.

Every change is validated here, audited, and applied in a transaction.
"""

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from django.conf import settings
from django.db import transaction
from django.utils.text import slugify

from apps.accounts.models import RoleGrant, User, normalize_email
from apps.events import hooks, preflight
from apps.events.models import (
    PHASE_ORDER,
    Criterion,
    CustomQuestion,
    Event,
    Phase,
    PhaseTransition,
    Prize,
    Track,
    Weighting,
)
from core import audit, outbox
from core.http import ApiError, conflict, not_found, unprocessable
from core.policy import Principal

# Phases before which the rubric may still change. Once judging starts it is
# locked, so every review is scored against the same criteria.
RUBRIC_EDITABLE = {Phase.DRAFT, Phase.REGISTRATION, Phase.SUBMISSIONS, Phase.ELIGIBILITY}

# Allowed moves: forward one phase, or skip registration from draft.
TRANSITIONS: dict[str, set[str]] = {
    phase: {PHASE_ORDER[i + 1]} for i, phase in enumerate(PHASE_ORDER[:-1])
}
TRANSITIONS[Phase.DRAFT].add(Phase.SUBMISSIONS)


@dataclass
class CriterionSpec:
    key: str
    label: str
    weight: Decimal
    min_score: int = 1
    max_score: int = 5
    description: str = ""
    anchors: dict[str, str] = field(default_factory=dict)
    is_gate: bool = False
    gate_threshold: Decimal | None = None
    is_bonus: bool = False


def get_event(event_id: str) -> Event:
    event = Event.objects.filter(pk=event_id).first()
    if event is None:
        raise not_found("No such event.")
    return event


def unique_slug(name: str) -> str:
    base = slugify(name)[:60] or "event"
    slug, n = base, 2
    while Event.objects.filter(slug=slug).exists():
        slug, n = f"{base}-{n}", n + 1
    return slug


# --- Rubric -------------------------------------------------------------------


def spec_problems(weighting: str, specs: list[CriterionSpec]) -> list[str]:
    problems: list[str] = []
    keys = [spec.key for spec in specs]
    if len(keys) != len(set(keys)):
        problems.append("Criterion keys must be unique.")
    scored = [spec for spec in specs if not spec.is_bonus and not spec.is_gate]
    if not scored:
        problems.append("Add at least one scored criterion.")
    for spec in specs:
        if spec.min_score >= spec.max_score:
            problems.append(f"'{spec.label}': the minimum must be below the maximum.")
        if spec.weight < 0:
            problems.append(f"'{spec.label}': weights cannot be negative.")
        if spec.is_gate and spec.is_bonus:
            problems.append(f"'{spec.label}': a criterion cannot be both a gate and a bonus.")
        if spec.is_gate and (
            spec.gate_threshold is None
            or not spec.min_score <= spec.gate_threshold <= spec.max_score
        ):
            problems.append(f"'{spec.label}': a gate needs a threshold inside its score range.")
    if weighting == Weighting.PERCENT and scored:
        total = sum((spec.weight for spec in scored), Decimal("0"))
        if total != Decimal("100"):
            problems.append(f"Percent weights of scored criteria must sum to 100 (now {total}).")
    if weighting == Weighting.MULTIPLIER and scored and all(spec.weight == 0 for spec in scored):
        problems.append("At least one scored criterion needs a weight above zero.")
    return problems


def specs_of(event: Event) -> list[CriterionSpec]:
    return [
        CriterionSpec(
            key=c.key,
            label=c.label,
            weight=c.weight,
            min_score=c.min_score,
            max_score=c.max_score,
            description=c.description,
            anchors=c.anchors,
            is_gate=c.is_gate,
            gate_threshold=c.gate_threshold,
            is_bonus=c.is_bonus,
        )
        for c in event.criteria.all()
    ]


def rubric_problems(event: Event) -> list[str]:
    return spec_problems(event.weighting, specs_of(event))


@transaction.atomic
def set_rubric(
    actor: Principal | None, event: Event, weighting: str, specs: list[CriterionSpec]
) -> list[Criterion]:
    if event.phase not in RUBRIC_EDITABLE:
        raise conflict(
            "rubric_locked",
            "The rubric is locked once judging starts, so every review uses the same criteria.",
        )
    if weighting not in Weighting.values:
        raise unprocessable("Unknown weighting mode.", {"weighting": weighting})
    problems = spec_problems(weighting, specs)
    if problems:
        raise unprocessable("The rubric is not valid.", {"problems": problems})
    event.weighting = weighting
    event.save(update_fields=["weighting", "updated_at"])
    event.criteria.all().delete()
    created = [
        Criterion.objects.create(
            event=event,
            key=spec.key,
            label=spec.label,
            description=spec.description,
            weight=spec.weight,
            min_score=spec.min_score,
            max_score=spec.max_score,
            anchors=spec.anchors,
            is_gate=spec.is_gate,
            gate_threshold=spec.gate_threshold,
            is_bonus=spec.is_bonus,
            order=index,
        )
        for index, spec in enumerate(specs)
    ]
    audit.record(
        "event.rubric_set",
        f"Rubric set: {len(created)} criteria ({weighting})",
        actor=actor,
        actor_role="organizer",
        event_id=event.pk,
        target=event,
        details={"criteria": [spec.key for spec in specs]},
    )
    return created


# --- Event, tracks, prizes, questions -----------------------------------------------

EDITABLE_FIELDS = {
    "name",
    "description",
    "registration_opens_at",
    "registration_closes_at",
    "submissions_open_at",
    "submissions_close_at",
    "judging_closes_at",
    "reviews_per_project",
    "max_load_per_judge",
    "blind_judging",
    "max_team_size",
    "allow_multiple_projects",
    "voting_mode",
    "voting_scheme",
    "qv_credits",
    "voting_opens_at",
    "voting_closes_at",
    "vote_burst_limit",
    "vote_trusted_networks",
    "vote_email_domains",
}


def clean_voting_lists(fields: dict[str, Any]) -> dict[str, Any]:
    """Normalise the trusted networks (CIDR) and allowed email domains, or refuse them."""
    import ipaddress

    if "vote_trusted_networks" in fields:
        networks = []
        for raw in fields["vote_trusted_networks"] or []:
            try:
                networks.append(str(ipaddress.ip_network(str(raw).strip(), strict=False)))
            except ValueError as exc:
                raise unprocessable(
                    f"Not a network range: {raw!r} (use CIDR, e.g. 10.0.0.0/16)"
                ) from exc
        fields["vote_trusted_networks"] = sorted(set(networks))
    if "vote_email_domains" in fields:
        domains = []
        for raw in fields["vote_email_domains"] or []:
            domain = str(raw).strip().lower().lstrip("@")
            if not domain or "." not in domain or " " in domain:
                raise unprocessable(f"Not an email domain: {raw!r}")
            domains.append(domain)
        fields["vote_email_domains"] = sorted(set(domains))
    return fields


@transaction.atomic
def create_event(actor: Principal, name: str, slug: str | None = None, **fields: Any) -> Event:
    unknown = set(fields) - EDITABLE_FIELDS
    if unknown:
        raise unprocessable("Unknown event fields.", {"fields": sorted(unknown)})
    if not name.strip():
        raise unprocessable("An event needs a name.")
    slug = slugify(slug) if slug else unique_slug(name)
    if Event.objects.filter(slug=slug).exists():
        raise conflict("slug_taken", "That address is already used by another event.")
    event = Event(name=name.strip(), slug=slug, **fields)
    _save_validated(event)
    if actor.user_id:
        RoleGrant.objects.get_or_create(
            user_id=actor.user_id,
            event=event,
            role="organizer",
            defaults={"created_by_id": actor.user_id},
        )
    audit.record(
        "event.created",
        f"Created event '{event.name}'",
        actor=actor,
        actor_role="organizer",
        event_id=event.pk,
        target=event,
    )
    return event


@transaction.atomic
def update_event(actor: Principal, event: Event, **fields: Any) -> Event:
    unknown = set(fields) - EDITABLE_FIELDS
    if unknown:
        raise unprocessable("Unknown event fields.", {"fields": sorted(unknown)})
    fields = clean_voting_lists(fields)
    changed = {key: value for key, value in fields.items() if getattr(event, key) != value}
    for key, value in changed.items():
        setattr(event, key, value)
    if changed:
        _save_validated(event)
        audit.record(
            "event.updated",
            f"Updated {', '.join(sorted(changed))}",
            actor=actor,
            actor_role="organizer",
            event_id=event.pk,
            target=event,
            details={key: str(value) for key, value in changed.items()},
        )
    return event


def _save_validated(event: Event) -> None:
    if (
        event.submissions_open_at
        and event.submissions_close_at
        and event.submissions_open_at >= event.submissions_close_at
    ):
        raise unprocessable("Submissions must open before they close.")
    if (
        event.voting_opens_at
        and event.voting_closes_at
        and event.voting_opens_at >= event.voting_closes_at
    ):
        raise unprocessable("Voting must open before it closes.")
    if event.reviews_per_project < 1:
        raise unprocessable("Each project needs at least one review.")
    event.save()


@transaction.atomic
def add_track(actor: Principal, event: Event, name: str, description: str = "") -> Track:
    name = name.strip()
    if not name:
        raise unprocessable("A track needs a name.")
    if event.tracks.filter(name=name).exists():
        raise conflict("track_exists", "This event already has a track with that name.")
    track = Track.objects.create(
        event=event, name=name, description=description, order=event.tracks.count()
    )
    audit.record(
        "event.track_added",
        f"Added track '{name}'",
        actor=actor,
        actor_role="organizer",
        event_id=event.pk,
        target=track,
    )
    return track


@transaction.atomic
def add_prize(
    actor: Principal,
    event: Event,
    name: str,
    rank: int,
    amount: str = "",
    track_id: str | None = None,
) -> Prize:
    if rank < 1:
        raise unprocessable("Prize rank starts at 1.")
    track = event.tracks.filter(pk=track_id).first() if track_id else None
    if track_id and track is None:
        raise unprocessable("That track does not belong to this event.")
    prize = Prize.objects.create(event=event, name=name, rank=rank, amount=amount, track=track)
    audit.record(
        "event.prize_added",
        f"Added prize '{name}' (rank {rank})",
        actor=actor,
        actor_role="organizer",
        event_id=event.pk,
        target=prize,
    )
    return prize


@transaction.atomic
def add_question(
    actor: Principal,
    event: Event,
    label: str,
    kind: str = "text",
    required: bool = False,
    choices: list[str] | None = None,
    help_text: str = "",
) -> CustomQuestion:
    if kind not in {"text", "long_text", "url", "choice", "bool"}:
        raise unprocessable("Unknown question type.", {"kind": kind})
    if kind == "choice" and not choices:
        raise unprocessable("A choice question needs choices.")
    question = CustomQuestion.objects.create(
        event=event,
        label=label,
        kind=kind,
        required=required,
        choices=choices or [],
        help_text=help_text,
        order=event.questions.count(),
    )
    audit.record(
        "event.question_added",
        f"Added submission question '{label}'",
        actor=actor,
        actor_role="organizer",
        event_id=event.pk,
        target=question,
    )
    return question


# --- People -----------------------------------------------------------------


@transaction.atomic
def grant_role(actor: Principal, event: Event, email: str, role: str, name: str = "") -> RoleGrant:
    if role not in {"participant", "judge", "organizer"}:
        raise unprocessable("Unknown role.", {"role": role})
    email = normalize_email(email)
    user = User.objects.filter(email=email).first()
    if user is None:
        user = User.objects.create_user(email, name=name)
    grant, created = RoleGrant.objects.get_or_create(
        user=user, event=event, role=role, defaults={"created_by_id": actor.user_id or ""}
    )
    if created:
        audit.record(
            "event.role_granted",
            f"Granted {role} role",
            actor=actor,
            actor_role="organizer",
            event_id=event.pk,
            target=grant,
            details={"user_id": user.pk, "role": role},
        )
        if role in {"judge", "organizer"}:
            _send_invitation(event, user, role)
    return grant


def _send_invitation(event: Event, user: User, role: str) -> None:
    """Invite by email. The link asks for a one-time sign-in link, so no password is sent."""
    what = "judge" if role == "judge" else "help organize"
    outbox.enqueue(
        "email",
        {
            "to": [user.email],
            "subject": f"You are invited to {what} {event.name}",
            "body": (
                f"You have been invited to {what} {event.name} on Raptor Desk.\n\n"
                f"Sign in with a one-time link: {settings.BASE_URL}/login/email\n"
                f"Use this address: {user.email}\n"
            ),
        },
        dedupe_key=f"invite:{event.pk}:{user.pk}:{role}",
    )


@transaction.atomic
def revoke_role(actor: Principal, event: Event, user_id: str, role: str) -> None:
    grant = RoleGrant.objects.filter(event=event, user_id=user_id, role=role).first()
    if grant is None:
        raise not_found("That person does not hold that role in this event.")
    if role == "organizer" and event.grants.filter(role="organizer").count() == 1:
        raise conflict("last_organizer", "An event must keep at least one organizer.")
    grant.delete()
    audit.record(
        "event.role_revoked",
        f"Revoked {role} role",
        actor=actor,
        actor_role="organizer",
        event_id=event.pk,
        details={"user_id": user_id, "role": role},
    )


# --- Lifecycle --------------------------------------------------------------


def preflight_for(event: Event, to_phase: str) -> list[preflight.Check]:
    return preflight.run(event, to_phase)


@transaction.atomic
def transition(
    actor: Principal, event: Event, to_phase: str, reason: str = "", override: bool = False
) -> PhaseTransition:
    if to_phase not in Phase.values:
        raise unprocessable("Unknown phase.", {"phase": to_phase})
    allowed = TRANSITIONS.get(event.phase, set())
    if to_phase not in allowed:
        raise conflict(
            "invalid_transition",
            f"An event in '{event.phase}' can move to: {', '.join(sorted(allowed)) or 'nothing'}.",
            {"from": event.phase, "to": to_phase},
        )
    checks = preflight_for(event, to_phase)
    blockers = preflight.blocking(checks)
    if blockers and not override:
        raise ApiError(
            409,
            "preflight_blocked",
            "Preflight checks block this phase change.",
            {"checks": [check.as_dict() for check in checks]},
        )
    if blockers and not reason.strip():
        raise unprocessable("Overriding a blocking check needs a written reason.")
    entry = PhaseTransition.objects.create(
        event=event,
        from_phase=event.phase,
        to_phase=to_phase,
        actor_id=actor.user_id or "",
        reason=reason,
        overridden=bool(blockers),
        preflight=[check.as_dict() for check in checks],
    )
    event.phase = to_phase
    event.save(update_fields=["phase", "updated_at"])
    hooks.entered(actor, event, to_phase)
    audit.record(
        "event.phase_changed",
        f"Phase changed from {entry.from_phase} to {to_phase}"
        + (" (blocking checks overridden)" if entry.overridden else ""),
        actor=actor,
        actor_role="organizer",
        event_id=event.pk,
        target=event,
        details={"reason": reason, "overridden": entry.overridden},
    )
    return entry
