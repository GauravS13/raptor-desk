import pytest
from django.test import Client

from apps.accounts.models import User
from apps.events import services
from core.policy import Principal


@pytest.fixture
def organizer_client(db) -> tuple[Client, str]:
    user = User.objects.create_user("org@example.org", name="Org Anizer")
    event = services.create_event(Principal(user_id=user.pk, email=user.email), "Spring Hack")
    services.add_track(Principal(user_id=user.pk), event, "Security")
    client = Client()
    client.force_login(user)
    return client, event.pk


def test_audit_trail_is_readable_and_filterable(organizer_client: tuple[Client, str]) -> None:
    client, event_id = organizer_client
    page = client.get(f"/o/events/{event_id}/audit")
    assert page.status_code == 200
    body = page.content.decode()
    assert "Created event &#x27;Spring Hack&#x27;" in body or "Created event 'Spring Hack'" in body
    assert "Org Anizer" in body
    filtered = client.get(f"/o/events/{event_id}/audit?action=event.track_added").content.decode()
    assert "Added track" in filtered and "Created event" not in filtered


def test_audit_page_is_organizer_only(organizer_client: tuple[Client, str]) -> None:
    _, event_id = organizer_client
    outsider = Client()
    outsider.force_login(User.objects.create_user("someone@example.org"))
    assert outsider.get(f"/o/events/{event_id}/audit").status_code == 403
