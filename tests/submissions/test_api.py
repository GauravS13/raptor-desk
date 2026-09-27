from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from django.test import Client

from apps.accounts.models import ApiToken, RoleGrant, User
from apps.events.models import CustomQuestion, Event, Track
from apps.submissions.models import Project, ProjectVersion
from apps.teams.models import Team, TeamMember
from core import clock

CLOSE = datetime(2030, 1, 1, 18, 0, tzinfo=UTC)


class Caller:
    def __init__(self, user: User | None) -> None:
        self.client = Client()
        self.headers = {}
        if user is not None:
            self.headers = {"HTTP_AUTHORIZATION": f"Bearer {ApiToken.issue(user, 't')[1]}"}

    def post(self, path: str, body: dict[str, Any] | None = None) -> Any:
        return self.client.post(path, body or {}, content_type="application/json", **self.headers)

    def put(self, path: str, body: dict[str, Any]) -> Any:
        return self.client.put(path, body, content_type="application/json", **self.headers)

    def get(self, path: str) -> Any:
        return self.client.get(path, **self.headers)


@pytest.fixture
def event(db) -> Event:
    return Event.objects.create(
        slug="hack", name="Hack", phase="submissions", submissions_close_at=CLOSE
    )


@pytest.fixture
def member(event: Event) -> Caller:
    user = User.objects.create_user("priya1@example.org")
    team = Team.objects.create(event=event, name="NorthKiln")
    TeamMember.objects.create(team=team, user=user, event=event, role="lead")
    RoleGrant.objects.create(user=user, event=event, role="participant")
    return Caller(user)


def test_closed_event_refuses_the_checkers_probe_with_409(event: Event, member: Caller) -> None:
    with clock.frozen(CLOSE + timedelta(minutes=1)):
        response = member.post(
            f"/api/events/{event.pk}/submissions",
            {"title": "dogfood-late-submission-probe", "summary": "probe"},
        )
    assert response.status_code == 409
    error = response.json()["error"]
    assert error["code"] == "submissions_closed"
    assert error["details"]["submissions_close_at"] == CLOSE.isoformat()
    assert "Location" not in response.headers
    assert Project.objects.count() == 0


def test_draft_submit_edit_resubmit_creates_versions(event: Event, member: Caller) -> None:
    created = member.post(
        f"/api/events/{event.pk}/submissions", {"title": "Glass Signal", "summary": "One line."}
    )
    assert created.status_code == 201
    project_id = created.json()["id"]
    assert created.json()["canonical"] is None
    assert created.json()["draft"]["n"] == 1

    submitted = member.post(f"/api/projects/{project_id}/submit")
    assert submitted.json()["canonical"]["n"] == 1

    edited = member.put(
        f"/api/projects/{project_id}/draft", {"name": "Glass Signal", "tagline": "Sharper."}
    )
    assert edited.json()["draft"]["n"] == 2
    assert edited.json()["canonical"]["tagline"] == "One line."

    resubmitted = member.post(f"/api/projects/{project_id}/submit")
    assert resubmitted.json()["canonical"]["n"] == 2
    assert resubmitted.json()["canonical"]["tagline"] == "Sharper."
    assert ProjectVersion.objects.filter(project_id=project_id).count() == 2

    assert member.post(f"/api/projects/{project_id}/submit").status_code == 409


def test_submit_and_create_in_one_call(event: Event, member: Caller) -> None:
    response = member.post(
        f"/api/events/{event.pk}/submissions", {"name": "Deep Compass", "submit": True}
    )
    assert response.status_code == 201
    assert response.json()["status"] == "submitted"


def test_required_questions_must_be_answered(event: Event, member: Caller) -> None:
    question = CustomQuestion.objects.create(event=event, label="Which stack?", required=True)
    project_id = member.post(f"/api/events/{event.pk}/submissions", {"name": "X"}).json()["id"]
    refused = member.post(f"/api/projects/{project_id}/submit")
    assert refused.status_code == 422
    assert refused.json()["error"]["details"]["missing"] == ["Which stack?"]
    member.put(
        f"/api/projects/{project_id}/draft", {"name": "X", "answers": {question.pk: "Django"}}
    )
    assert member.post(f"/api/projects/{project_id}/submit").status_code == 200


def test_who_may_submit(event: Event) -> None:
    assert (
        Caller(None).post(f"/api/events/{event.pk}/submissions", {"name": "X"}).status_code == 401
    )
    outsider = Caller(User.objects.create_user("outsider@example.org"))
    assert outsider.post(f"/api/events/{event.pk}/submissions", {"name": "X"}).status_code == 403
    teamless = User.objects.create_user("teamless@example.org")
    RoleGrant.objects.create(user=teamless, event=event, role="participant")
    response = Caller(teamless).post(f"/api/events/{event.pk}/submissions", {"name": "X"})
    assert response.status_code == 403
    assert "team" in response.json()["error"]["message"]


def test_other_teams_cannot_touch_a_project(event: Event, member: Caller) -> None:
    project_id = member.post(f"/api/events/{event.pk}/submissions", {"name": "X"}).json()["id"]
    rival_user = User.objects.create_user("rival@example.org")
    rival_team = Team.objects.create(event=event, name="Rivals")
    TeamMember.objects.create(team=rival_team, user=rival_user, event=event, role="lead")
    rival = Caller(rival_user)
    assert rival.post(f"/api/projects/{project_id}/submit").status_code == 403
    assert rival.post(f"/api/projects/{project_id}/withdraw").status_code == 403
    assert rival.get(f"/api/projects/{project_id}").status_code == 404  # drafts stay private


def test_gallery_is_public_and_filters(event: Event, member: Caller) -> None:
    security = Track.objects.create(event=event, name="Security")
    member.post(
        f"/api/events/{event.pk}/submissions",
        {
            "name": "Glass Signal",
            "tagline": "Signals",
            "track_id": security.pk,
            "tech_tags": ["Rust"],
            "submit": True,
        },
    )
    anon = Caller(None)
    everything = anon.get(f"/api/events/{event.pk}/projects").json()
    assert [p["canonical"]["name"] for p in everything] == ["Glass Signal"]
    assert everything[0]["draft"] is None
    assert len(anon.get(f"/api/events/{event.pk}/projects?q=glass").json()) == 1
    assert anon.get(f"/api/events/{event.pk}/projects?q=nothing").json() == []
    assert len(anon.get(f"/api/events/{event.pk}/projects?track={security.pk}").json()) == 1
    assert len(anon.get(f"/api/events/{event.pk}/projects?tag=rust").json()) == 1
    assert anon.get(f"/api/events/{event.pk}/projects?tag=go").json() == []


def test_drafts_and_withdrawn_projects_stay_out_of_the_gallery(
    event: Event, member: Caller
) -> None:
    project_id = member.post(f"/api/events/{event.pk}/submissions", {"name": "X"}).json()["id"]
    assert Caller(None).get(f"/api/events/{event.pk}/projects").json() == []
    member.post(f"/api/projects/{project_id}/submit")
    member.post(f"/api/projects/{project_id}/withdraw")
    assert Caller(None).get(f"/api/events/{event.pk}/projects").json() == []


def test_draft_event_gallery_is_hidden(db) -> None:
    draft = Event.objects.create(slug="secret", name="Secret")
    assert Caller(None).get(f"/api/events/{draft.pk}/projects").status_code == 404
