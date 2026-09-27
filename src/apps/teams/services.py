"""Team formation by invite link. Shared by the team pages and the REST API."""

import secrets
from datetime import timedelta

from django.conf import settings
from django.db import IntegrityError, transaction

from apps.accounts.models import ApiToken, RoleGrant
from apps.events.models import Event, Phase
from apps.teams.models import Invite, MemberRole, Team, TeamMember
from core import audit, clock
from core.http import ApiError, conflict, forbidden, not_found, unprocessable
from core.policy import Principal

FORMATION_PHASES = {Phase.REGISTRATION, Phase.SUBMISSIONS}
INVITE_TTL = timedelta(days=7)


def formation_is_open(event: Event) -> bool:
    if event.phase not in FORMATION_PHASES:
        return False
    return event.submissions_close_at is None or clock.now() < event.submissions_close_at


def _require_formation_open(event: Event) -> None:
    if not formation_is_open(event):
        raise conflict("teams_closed", "Teams can only be formed while registration is open.")


def team_of(principal: Principal, event: Event) -> Team | None:
    if not principal.user_id:
        return None
    membership = (
        TeamMember.objects.select_related("team")
        .filter(event=event, user_id=principal.user_id)
        .first()
    )
    return membership.team if membership else None


def get_team(team_id: str) -> Team:
    team = Team.objects.select_related("event").filter(pk=team_id).first()
    if team is None:
        raise not_found("No such team.")
    return team


def _membership(principal: Principal, team: Team) -> TeamMember:
    membership = TeamMember.objects.filter(team=team, user_id=principal.user_id).first()
    if membership is None:
        raise forbidden("You are not a member of this team.")
    return membership


def _grant_participant(user_id: str, event: Event) -> None:
    RoleGrant.objects.get_or_create(
        user_id=user_id, event=event, role="participant", defaults={"created_by_id": user_id}
    )


@transaction.atomic
def create_team(principal: Principal, event: Event, name: str) -> Team:
    if not principal.user_id:
        raise forbidden("Sign in to create a team.")
    _require_formation_open(event)
    name = name.strip()
    if not name:
        raise unprocessable("A team needs a name.")
    if team_of(principal, event) is not None:
        raise conflict("already_in_team", "You are already in a team for this event.")
    if Team.objects.filter(event=event, name=name).exists():
        raise conflict("team_name_taken", "Another team in this event uses that name.")
    team = Team.objects.create(event=event, name=name)
    TeamMember.objects.create(
        team=team, user_id=principal.user_id, event=event, role=MemberRole.LEAD
    )
    _grant_participant(principal.user_id, event)
    audit.record(
        "team.created",
        f"Created team '{name}'",
        actor=principal,
        actor_role="participant",
        event_id=event.pk,
        target=team,
    )
    return team


@transaction.atomic
def create_invite(principal: Principal, team: Team) -> tuple[Invite, str, str]:
    """Return the invite, the raw token (shown once) and the shareable link."""
    membership = _membership(principal, team)
    if membership.role != MemberRole.LEAD:
        raise forbidden("Only the team lead can create invite links.")
    _require_formation_open(team.event)
    spaces = team.event.max_team_size - team.members.count()
    if spaces <= 0:
        raise conflict("team_full", "The team is already at the maximum size.")
    raw = secrets.token_urlsafe(24)
    invite = Invite.objects.create(
        team=team,
        token_hash=ApiToken.hash(raw),
        created_by_id=principal.user_id or "",
        expires_at=clock.now() + INVITE_TTL,
        uses_left=spaces,
    )
    audit.record(
        "team.invite_created",
        f"Invite link created for '{team.name}' ({spaces} places)",
        actor=principal,
        actor_role="participant",
        event_id=team.event_id,
        target=invite,
    )
    return invite, raw, f"{settings.BASE_URL}/join/{raw}"


def find_invite(raw: str) -> Invite:
    invite = (
        Invite.objects.select_related("team__event").filter(token_hash=ApiToken.hash(raw)).first()
    )
    if invite is None or not invite.is_usable:
        raise ApiError(410, "invite_invalid", "This invite link is no longer valid.")
    return invite


@transaction.atomic
def join_with_invite(principal: Principal, raw: str) -> Team:
    if not principal.user_id:
        raise forbidden("Sign in to join a team.")
    invite = find_invite(raw)
    team = invite.team
    event = team.event
    _require_formation_open(event)
    if team_of(principal, event) is not None:
        raise conflict("already_in_team", "You are already in a team for this event.")
    if team.members.count() >= event.max_team_size:
        raise conflict("team_full", "The team is already at the maximum size.")
    try:
        TeamMember.objects.create(team=team, user_id=principal.user_id, event=event)
    except IntegrityError as exc:  # a concurrent join won the race
        raise conflict("already_in_team", "You are already in a team for this event.") from exc
    Invite.objects.filter(pk=invite.pk).update(uses_left=invite.uses_left - 1)
    _grant_participant(principal.user_id, event)
    audit.record(
        "team.joined",
        f"Joined team '{team.name}' by invite",
        actor=principal,
        actor_role="participant",
        event_id=event.pk,
        target=team,
    )
    return team


@transaction.atomic
def leave_team(principal: Principal, team: Team) -> None:
    membership = _membership(principal, team)
    _require_formation_open(team.event)
    others = team.members.exclude(pk=membership.pk).order_by("joined_at")
    if not others.exists() and getattr(team, "projects", None) and team.projects.exists():
        raise conflict("team_has_project", "The last member cannot leave a team with a project.")
    membership.delete()
    if others.exists():
        if membership.role == MemberRole.LEAD:
            successor = others.first()
            TeamMember.objects.filter(pk=successor.pk).update(role=MemberRole.LEAD)
    else:
        team.delete()
    audit.record(
        "team.left",
        f"Left team '{team.name}'",
        actor=principal,
        actor_role="participant",
        event_id=team.event_id,
    )
