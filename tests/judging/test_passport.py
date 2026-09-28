from io import StringIO
from pathlib import Path

import pytest
from django.core.management import call_command
from django.test import Client

from apps.events import services as event_services
from apps.events.models import Event, Phase
from apps.seed.demo import SEED_LOGINS
from core import signing
from core.models import AuditEvent
from core.policy import Principal

ORGANIZER = Principal(user_id="organizer", email="organizer@raptor-desk.local")
TOKENS = {login.role: login.token for login in SEED_LOGINS}


def bearer(role: str) -> dict[str, str]:
    return {"HTTP_AUTHORIZATION": f"Bearer {TOKENS[role]}"}


@pytest.fixture
def published(db, settings, tmp_path) -> Event:
    settings.DATA_DIR = tmp_path
    signing.signing_key.cache_clear()
    fixtures = Path(settings.REPO_DIR) / "data" / "fixtures.json"
    call_command("seed", fixtures=str(fixtures), stdout=StringIO())
    event = Event.objects.get(pk="evt_01")
    event_services.transition(ORGANIZER, event, Phase.DELIBERATION, reason="Judging closed")
    event_services.transition(
        ORGANIZER, event, Phase.PUBLISHED, reason="Publishing for the test.", override=True
    )
    yield event
    signing.signing_key.cache_clear()


def test_a_passport_is_private_until_the_judge_opts_in(published: Event) -> None:
    client = Client()
    assert client.get("/judges/jdg_24/passport").status_code == 404
    assert client.get("/api/judges/jdg_24/passport").status_code == 404

    opted = client.post(
        "/api/me/passport", {"public": True}, content_type="application/json", **bearer("judge_b")
    )
    assert opted.json() == {"public": True}
    body = client.get("/api/judges/jdg_24/passport").json()
    assert {c["event_id"] for c in body["certificates"]} == {"evt_01", "evt_archive"}
    assert all(c["verify_url"].startswith("/verify/") for c in body["certificates"])
    page = client.get("/judges/jdg_24/passport").content.decode()
    assert "Judge passport" in page and "score" not in page.lower().replace(
        "never shows scores", ""
    )
    assert AuditEvent.objects.filter(action="judging.passport_changed").exists()


def test_the_demo_judge_a_passport_is_public_by_seed(published: Event) -> None:
    assert Client().get("/api/judges/jdg_26/passport").status_code == 200


def test_a_judge_can_only_change_their_own_passport(published: Event) -> None:
    client = Client()
    response = client.post(
        "/api/me/passport",
        {"public": True},
        content_type="application/json",
        **bearer("participant"),
    )
    assert response.status_code == 403
    assert client.get("/api/judges/jdg_24/passport").status_code == 404


def test_the_queue_offers_the_switch(published: Event) -> None:
    client = Client()
    client.post("/login", {"email": "diego.herrera@example.org", "password": "raptor-demo-2026"})
    assert "Make my passport public" in client.get("/judge").content.decode()
    client.post("/judge/passport", {"public": "1"})
    assert "Make it private" in client.get("/judge").content.decode()
