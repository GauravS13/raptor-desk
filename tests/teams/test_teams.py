import re
from datetime import UTC, datetime, timedelta

import pytest
from django.test import Client

from apps.accounts.models import ApiToken, RoleGrant, User
from apps.events.models import Event
from apps.teams.models import Invite, Team, TeamMember
from core import clock
from core.actions import parity_gaps

CLOSE = datetime(2030, 1, 1, 18, 0, tzinfo=UTC)


@pytest.fixture
def event(db) -> Event:
    return Event.objects.create(
        slug="hack", name="Hack", phase="registration", submissions_close_at=CLOSE, max_team_size=2
    )


def api(email: str) -> tuple[Client, dict[str, str], User]:
    user = User.objects.filter(email=email).first() or User.objects.create_user(email)
    _, raw = ApiToken.issue(user, "t")
    return Client(), {"HTTP_AUTHORIZATION": f"Bearer {raw}"}, user


def post(client: Client, headers: dict[str, str], path: str, body: dict | None = None):
    return client.post(path, body or {}, content_type="application/json", **headers)


def test_parity_still_holds() -> None:
    assert parity_gaps() == []


def test_create_invite_join_flow_over_the_api(event: Event) -> None:
    lead_client, lead, lead_user = api("lead@example.org")
    created = post(lead_client, lead, f"/api/events/{event.pk}/teams", {"name": "NorthKiln"})
    assert created.status_code == 201
    team_id = created.json()["id"]
    assert RoleGrant.objects.filter(user=lead_user, event=event, role="participant").exists()

    invite = post(lead_client, lead, f"/api/teams/{team_id}/invites")
    assert invite.status_code == 201
    body = invite.json()
    assert body["uses_left"] == 1
    assert body["url"].endswith(body["token"])

    mate_client, mate, _ = api("mate@example.org")
    joined = post(mate_client, mate, f"/api/invites/{body['token']}/accept")
    assert joined.status_code == 200
    assert {m["email"] for m in joined.json()["members"]} == {
        "lead@example.org",
        "mate@example.org",
    }

    third_client, third, _ = api("third@example.org")
    refused = post(third_client, third, f"/api/invites/{body['token']}/accept")
    assert refused.status_code == 410  # the invite had one place and it is used


def test_one_team_per_person_per_event(event: Event) -> None:
    client, headers, _ = api("lead@example.org")
    assert post(client, headers, f"/api/events/{event.pk}/teams", {"name": "A"}).status_code == 201
    second = post(client, headers, f"/api/events/{event.pk}/teams", {"name": "B"})
    assert second.status_code == 409
    assert second.json()["error"]["code"] == "already_in_team"


def test_only_the_lead_creates_invites_and_team_size_is_enforced(event: Event) -> None:
    lead_client, lead, _ = api("lead@example.org")
    team_id = post(lead_client, lead, f"/api/events/{event.pk}/teams", {"name": "A"}).json()["id"]
    token = post(lead_client, lead, f"/api/teams/{team_id}/invites").json()["token"]
    mate_client, mate, _ = api("mate@example.org")
    post(mate_client, mate, f"/api/invites/{token}/accept")

    assert post(mate_client, mate, f"/api/teams/{team_id}/invites").status_code == 403
    full = post(lead_client, lead, f"/api/teams/{team_id}/invites")
    assert full.status_code == 409
    assert full.json()["error"]["code"] == "team_full"


def test_formation_closes_with_the_deadline(event: Event) -> None:
    client, headers, _ = api("late@example.org")
    with clock.frozen(CLOSE + timedelta(seconds=1)):
        response = post(client, headers, f"/api/events/{event.pk}/teams", {"name": "Late"})
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "teams_closed"


def test_expired_invite_is_refused(event: Event) -> None:
    lead_client, lead, _ = api("lead@example.org")
    team_id = post(lead_client, lead, f"/api/events/{event.pk}/teams", {"name": "A"}).json()["id"]
    token = post(lead_client, lead, f"/api/teams/{team_id}/invites").json()["token"]
    Invite.objects.update(expires_at=clock.now() - timedelta(seconds=1))
    mate_client, mate, _ = api("mate@example.org")
    assert post(mate_client, mate, f"/api/invites/{token}/accept").status_code == 410


def test_leaving_passes_the_lead_and_the_last_member_dissolves_the_team(event: Event) -> None:
    lead_client, lead, _ = api("lead@example.org")
    team_id = post(lead_client, lead, f"/api/events/{event.pk}/teams", {"name": "A"}).json()["id"]
    token = post(lead_client, lead, f"/api/teams/{team_id}/invites").json()["token"]
    mate_client, mate, mate_user = api("mate@example.org")
    post(mate_client, mate, f"/api/invites/{token}/accept")

    assert post(lead_client, lead, f"/api/teams/{team_id}/leave").status_code == 204
    assert TeamMember.objects.get(team_id=team_id).user == mate_user
    assert TeamMember.objects.get(team_id=team_id).role == "lead"
    assert post(mate_client, mate, f"/api/teams/{team_id}/leave").status_code == 204
    assert not Team.objects.filter(pk=team_id).exists()


def test_team_pages_create_and_join_by_link(event: Event) -> None:
    lead = User.objects.create_user("lead@example.org")
    client = Client()
    client.force_login(lead)
    assert client.post(f"/events/{event.pk}/team", {"name": "NorthKiln"}).status_code == 302
    team = Team.objects.get(name="NorthKiln")
    client.post(f"/teams/{team.pk}/invites")
    page = client.get(f"/events/{event.pk}/team")
    link = re.search(rb"/join/([\w-]+)", page.content).group(1).decode()

    mate = User.objects.create_user("mate@example.org")
    mate_client = Client()
    mate_client.force_login(mate)
    assert mate_client.get(f"/join/{link}").status_code == 200
    assert mate_client.post(f"/join/{link}").status_code == 302
    assert team.members.count() == 2
    assert Client().get(f"/join/{link}").status_code == 302  # anonymous: sent to sign in


def test_organizers_list_teams_but_participants_cannot(event: Event) -> None:
    client, headers, _ = api("lead@example.org")
    post(client, headers, f"/api/events/{event.pk}/teams", {"name": "A"})
    assert client.get(f"/api/events/{event.pk}/teams", **headers).status_code == 403
    org_client, org, org_user = api("org@example.org")
    RoleGrant.objects.create(user=org_user, event=event, role="organizer")
    listed = org_client.get(f"/api/events/{event.pk}/teams", **org)
    assert listed.status_code == 200
    assert [t["name"] for t in listed.json()] == ["A"]
