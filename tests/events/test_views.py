import pytest
from django.test import Client

from apps.accounts.models import RoleGrant, User
from apps.events.models import Event
from core.actions import parity_gaps


@pytest.fixture
def admin_client(db) -> Client:
    client = Client()
    client.force_login(User.objects.create_user("admin@example.org", is_admin=True))
    return client


def create_event(
    client: Client, name: str = "Spring Hack", template: str = "dogfood-2026"
) -> Event:
    response = client.post(
        "/o/events/new",
        {"name": name, "template": template, "submissions_close_at": "2030-01-01T18:00"},
    )
    assert response.status_code == 302, response.content
    return Event.objects.get(name=name)


def test_every_console_action_has_an_api_twin() -> None:
    assert parity_gaps() == []


def test_anonymous_visitors_are_sent_to_login(db) -> None:
    response = Client().get("/o/")
    assert response.status_code == 302
    assert response["Location"].startswith("/login?next=")


def test_admin_creates_event_from_template_in_the_console(admin_client: Client) -> None:
    event = create_event(admin_client)
    assert event.template_key == "dogfood-2026"
    assert event.submissions_close_at.isoformat() == "2030-01-01T18:00:00+00:00"
    page = admin_client.get(f"/o/events/{event.pk}")
    assert page.status_code == 200
    assert b"Move to: Registration" in page.content


def test_non_organizer_gets_403_page(admin_client: Client) -> None:
    event = create_event(admin_client)
    judge = User.objects.create_user("judge@example.org")
    RoleGrant.objects.create(user=judge, event=event, role="judge")
    client = Client()
    client.force_login(judge)
    response = client.get(f"/o/events/{event.pk}/settings")
    assert response.status_code == 403
    assert b"Access denied" in response.content


def test_rubric_formset_saves_and_reports_problems(admin_client: Client) -> None:
    event = create_event(admin_client, template="")
    form = {
        "weighting": "percent",
        "form-TOTAL_FORMS": "3",
        "form-INITIAL_FORMS": "0",
        "form-0-key": "impact",
        "form-0-label": "Impact",
        "form-0-weight": "70",
        "form-0-min_score": "1",
        "form-0-max_score": "5",
        "form-1-key": "craft",
        "form-1-label": "Craft",
        "form-1-weight": "30",
        "form-1-min_score": "1",
        "form-1-max_score": "5",
    }
    response = admin_client.post(f"/o/events/{event.pk}/rubric", form)
    assert response.status_code == 302
    assert list(event.criteria.values_list("key", flat=True)) == ["impact", "craft"]

    form["form-1-weight"] = "20"
    response = admin_client.post(f"/o/events/{event.pk}/rubric", form)
    assert response.status_code == 422
    assert b"sum to 100" in response.content


def test_people_page_grants_and_revokes(admin_client: Client) -> None:
    event = create_event(admin_client)
    response = admin_client.post(
        f"/o/events/{event.pk}/people", {"email": "judge@example.org", "role": "judge"}
    )
    assert response.status_code == 302
    grant = RoleGrant.objects.get(event=event, role="judge")
    response = admin_client.post(f"/o/events/{event.pk}/people/{grant.user_id}/judge/revoke")
    assert response.status_code == 302
    assert not RoleGrant.objects.filter(event=event, role="judge").exists()


def test_structure_page_adds_tracks_prizes_and_questions(admin_client: Client) -> None:
    event = create_event(admin_client, template="")
    admin_client.post(f"/o/events/{event.pk}/tracks", {"name": "Security"})
    admin_client.post(f"/o/events/{event.pk}/prizes", {"name": "Best in show", "rank": "1"})
    admin_client.post(
        f"/o/events/{event.pk}/questions",
        {"label": "Which stack?", "kind": "choice", "choices": "Django\nRails"},
    )
    assert event.tracks.get().name == "Security"
    assert event.prizes.get().name == "Best in show"
    assert event.questions.get().choices == ["Django", "Rails"]


def test_blocked_phase_change_shows_the_reason(admin_client: Client) -> None:
    event = create_event(admin_client)
    Event.objects.filter(pk=event.pk).update(phase="eligibility")
    response = admin_client.post(f"/o/events/{event.pk}/phase", {"to": "judging"}, follow=True)
    assert b"Preflight checks block this phase change" in response.content
    event.refresh_from_db()
    assert event.phase == "eligibility"
