from datetime import datetime

from django.http import HttpRequest
from ninja import Router, Schema, Status

from apps.events import policies as event_policies
from apps.events.services import get_event
from apps.teams import services
from apps.teams.models import Team
from core.actions import api_action
from core.http import not_found
from core.policy import Rule, define, get_principal, policy

TEAMS_SELF = define(
    "teams.self",
    Rule(authenticated=True, description="Signed-in users manage their own team membership."),
)

router = Router(tags=["teams"])


class MemberOut(Schema):
    user_id: str
    email: str
    name: str
    role: str


class TeamOut(Schema):
    id: str
    event_id: str
    name: str
    members: list[MemberOut]


class TeamIn(Schema):
    name: str


class InviteOut(Schema):
    invite_id: str
    token: str
    url: str
    expires_at: datetime
    uses_left: int


def team_out(team: Team) -> TeamOut:
    members = [
        MemberOut(user_id=m.user_id, email=m.user.email, name=m.user.name, role=m.role)
        for m in team.members.select_related("user")
    ]
    return TeamOut(id=team.pk, event_id=team.event_id, name=team.name, members=members)


@router.post("/events/{event_id}/teams", response={201: TeamOut})
@api_action("team.create")
@policy(TEAMS_SELF)
def create_team(request: HttpRequest, event_id: str, payload: TeamIn) -> Status[TeamOut]:
    team = services.create_team(get_principal(request), get_event(event_id), payload.name)
    return Status(201, team_out(team))


@router.get("/events/{event_id}/teams/mine", response=TeamOut)
@policy(TEAMS_SELF)
def my_team(request: HttpRequest, event_id: str) -> TeamOut:
    team = services.team_of(get_principal(request), get_event(event_id))
    if team is None:
        raise not_found("You are not in a team for this event.")
    return team_out(team)


@router.get("/events/{event_id}/teams", response=list[TeamOut])
@policy(event_policies.EVENTS_MANAGE)
def list_teams(request: HttpRequest, event_id: str) -> list[TeamOut]:
    return [team_out(team) for team in get_event(event_id).teams.all()]


@router.post("/teams/{team_id}/invites", response={201: InviteOut})
@api_action("team.invite")
@policy(TEAMS_SELF)
def create_invite(request: HttpRequest, team_id: str) -> Status[InviteOut]:
    invite, raw, url = services.create_invite(get_principal(request), services.get_team(team_id))
    return Status(
        201,
        InviteOut(
            invite_id=invite.pk,
            token=raw,
            url=url,
            expires_at=invite.expires_at,
            uses_left=invite.uses_left,
        ),
    )


@router.post("/invites/{token}/accept", response=TeamOut)
@api_action("team.join")
@policy(TEAMS_SELF)
def accept_invite(request: HttpRequest, token: str) -> TeamOut:
    return team_out(services.join_with_invite(get_principal(request), token))


@router.post("/teams/{team_id}/leave", response={204: None})
@api_action("team.leave")
@policy(TEAMS_SELF)
def leave(request: HttpRequest, team_id: str) -> Status[None]:
    services.leave_team(get_principal(request), services.get_team(team_id))
    return Status(204, None)
