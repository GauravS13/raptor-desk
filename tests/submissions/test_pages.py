from datetime import UTC, datetime, timedelta

import pytest
from django.test import Client

from apps.accounts.models import RoleGrant, User
from apps.events.models import CustomQuestion, Event
from apps.submissions.models import Project, ProjectVersion
from apps.teams.models import Team, TeamMember
from core import clock
from core.actions import parity_gaps

CLOSE = datetime(2030, 1, 1, 18, 0, tzinfo=UTC)


@pytest.fixture
def event(db) -> Event:
    return Event.objects.create(
        id="evt_01",
        slug="hack",
        name="Sample Hack",
        phase="submissions",
        submissions_close_at=CLOSE,
    )


@pytest.fixture
def member_client(event: Event) -> Client:
    user = User.objects.create_user("priya1@example.org")
    team = Team.objects.create(event=event, name="NorthKiln")
    TeamMember.objects.create(team=team, user=user, event=event, role="lead")
    RoleGrant.objects.create(user=user, event=event, role="participant")
    client = Client()
    client.force_login(user)
    return client


def published(event: Event, name: str, order: int) -> Project:
    team = Team.objects.create(event=event, name=f"Team {name}")
    project = Project.objects.create(
        event=event, team=team, status="submitted", gallery_order=order
    )
    version = ProjectVersion.objects.create(
        project=project, n=1, name=name, submitted_at=clock.now()
    )
    project.canonical_version = version
    project.save()
    return project


def test_parity_holds_with_submission_pages() -> None:
    assert parity_gaps() == []


def test_gallery_is_public_ordered_and_lists_titles(event: Event) -> None:
    published(event, "Glass Signal", 1)
    published(event, "Small Meadow", 2)
    response = Client().get(f"/events/{event.pk}/projects")
    assert response.status_code == 200
    body = response.content.decode()
    assert body.index("Glass Signal") < body.index("Small Meadow")
    assert "2 projects" in body


def test_gallery_search_keeps_the_same_url(event: Event) -> None:
    published(event, "Glass Signal", 1)
    published(event, "Small Meadow", 2)
    body = Client().get(f"/events/{event.pk}/projects?q=meadow").content.decode()
    assert "Small Meadow" in body and "Glass Signal" not in body


def test_home_and_event_page_are_public(event: Event) -> None:
    assert b"Sample Hack" in Client().get("/").content
    page = Client().get(f"/events/{event.pk}")
    assert page.status_code == 200
    assert b"How projects are judged" not in page.content  # no rubric yet


def test_submission_form_draft_then_submit(event: Event, member_client: Client) -> None:
    form = {"name": "Glass Signal", "tagline": "Signals", "tech_tags": "Rust, WASM"}
    assert member_client.post(f"/events/{event.pk}/submit", form).status_code == 302
    project = Project.objects.get()
    assert project.status == "draft"
    assert project.versions.get().tech_tags == ["Rust", "WASM"]

    response = member_client.post(f"/events/{event.pk}/submit", {**form, "submit": "1"})
    assert response.status_code == 302
    assert response["Location"] == f"/projects/{project.pk}"
    project.refresh_from_db()
    assert project.status == "submitted"
    assert b"Glass Signal" in Client().get(f"/projects/{project.pk}").content


def test_submission_form_reports_missing_required_answers(
    event: Event, member_client: Client
) -> None:
    question = CustomQuestion.objects.create(event=event, label="Which stack?", required=True)
    response = member_client.post(f"/events/{event.pk}/submit", {"name": "X", "submit": "1"})
    assert response.status_code == 422
    assert b"Which stack?" in response.content
    ok = member_client.post(
        f"/events/{event.pk}/submit", {"name": "X", f"q_{question.pk}": "Django", "submit": "1"}
    )
    assert ok.status_code == 302


def test_closed_event_form_is_read_only(event: Event, member_client: Client) -> None:
    with clock.frozen(CLOSE + timedelta(seconds=1)):
        page = member_client.get(f"/events/{event.pk}/submit")
        assert b"Submissions are closed." in page.content
        refused = member_client.post(f"/events/{event.pk}/submit", {"name": "Late", "submit": "1"})
    assert refused.status_code == 409
    assert Project.objects.count() == 0


def test_user_without_a_team_gets_a_clear_403(event: Event) -> None:
    client = Client()
    client.force_login(User.objects.create_user("loner@example.org"))
    response = client.get(f"/events/{event.pk}/submit")
    assert response.status_code == 403
