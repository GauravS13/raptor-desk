import copy
import hashlib
import hmac
import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from io import StringIO
from pathlib import Path

import pytest
from django.conf import settings
from django.core.management import call_command
from django.test import Client

from apps.events.models import Event
from apps.integrations import webhooks
from apps.seed.demo import SEED_LOGINS
from core.models import OutboxMessage

TOKENS = {login.role: login.token for login in SEED_LOGINS}


def bearer(role: str) -> dict[str, str]:
    return {"HTTP_AUTHORIZATION": f"Bearer {TOKENS[role]}"}


@pytest.fixture
def seeded(db) -> None:
    fixtures = Path(settings.REPO_DIR) / "data" / "fixtures.json"
    call_command("seed", fixtures=str(fixtures), stdout=StringIO())


@pytest.fixture
def client() -> Client:
    return Client()


class _Listener(BaseHTTPRequestHandler):
    received: list[tuple[bytes, dict[str, str]]] = []

    def do_POST(self) -> None:
        body = self.rfile.read(int(self.headers["Content-Length"]))
        _Listener.received.append((body, dict(self.headers)))
        self.send_response(204)
        self.end_headers()

    def log_message(self, *args: object) -> None:
        pass


@pytest.fixture
def listener():
    _Listener.received = []
    server = HTTPServer(("127.0.0.1", 0), _Listener)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_port}/hook", _Listener.received
    server.shutdown()


def _deliver_pending() -> None:
    for message in OutboxMessage.objects.filter(kind="webhook", done_at__isnull=True):
        webhooks.deliver(message.payload)


def test_only_organizers_register_webhooks(seeded, client) -> None:
    body = {"url": "https://example.org/hook", "events": ["comment.created"]}
    created = client.post(
        "/api/webhooks", body, content_type="application/json", **bearer("organizer")
    )
    assert created.status_code == 201
    assert created.json()["id"] and len(created.json()["secret"]) >= 32
    refused = client.post(
        "/api/webhooks", body, content_type="application/json", **bearer("participant")
    )
    assert refused.status_code == 403
    bad = client.post(
        "/api/webhooks",
        {"url": "ftp://x", "events": ["comment.created"]},
        content_type="application/json",
        **bearer("organizer"),
    )
    assert bad.status_code == 422


def test_a_comment_fires_a_signed_webhook(seeded, client, listener) -> None:
    url, received = listener
    secret = client.post(
        "/api/webhooks",
        {"url": url, "events": ["comment.created"], "event_id": "evt_vote"},
        content_type="application/json",
        **bearer("organizer"),
    ).json()["secret"]
    client.post(
        "/api/projects/prj_v02/comments",
        {"body": "Great demo"},
        content_type="application/json",
        **bearer("participant"),
    )
    _deliver_pending()
    assert len(received) == 1
    raw, headers = received[0]
    payload = json.loads(raw)
    assert payload["event"] == "comment.created" and payload["data"]["project"] == "prj_v02"
    expected = "sha256=" + hmac.new(secret.encode(), raw, hashlib.sha256).hexdigest()
    assert headers["X-Dogfood-Signature"] == expected
    wrong = "sha256=" + hmac.new(b"wrong", raw, hashlib.sha256).hexdigest()
    assert headers["X-Dogfood-Signature"] != wrong


def test_the_embed_can_be_framed_and_nothing_else_can(seeded, client) -> None:
    embed = client.get("/embed/events/evt_01/gallery")
    assert embed.status_code == 200 and embed["Content-Type"].startswith("text/html")
    assert "Glass Signal" in embed.content.decode()
    assert "X-Frame-Options" not in embed
    assert "frame-ancestors *" in embed["Content-Security-Policy"]
    private = client.get("/o/")
    assert private["X-Frame-Options"] == "DENY"
    assert "frame-ancestors 'none'" in private["Content-Security-Policy"]


def test_export_is_for_organizers(seeded, client) -> None:
    assert client.get("/api/events/evt_01/export").status_code == 401
    assert client.get("/api/events/evt_01/export", **bearer("participant")).status_code == 403
    bundle = client.get("/api/events/evt_01/export", **bearer("organizer")).json()
    for key in ("event", "tracks", "criteria", "teams", "projects", "reviews"):
        assert bundle[key], key
    assert len(bundle["manifest"]["sha256"]) == 64
    assert bundle["manifest"]["counts"]["projects"] == 40


def _summary(bundle: dict) -> tuple:
    titles = sorted(v["name"] for p in bundle["projects"] for v in p["versions"])
    scores = sorted(json.dumps(r["scores"], sort_keys=True) for r in bundle["reviews"])
    return bundle["manifest"]["counts"], titles, scores


def test_import_round_trip_keeps_every_count_title_and_score(seeded, client) -> None:
    bundle = client.get("/api/events/evt_01/export", **bearer("organizer")).json()
    created = client.post(
        "/api/event-bundles",
        {"bundle": bundle, "name": "Round trip test"},
        content_type="application/json",
        **bearer("organizer"),
    )
    assert created.status_code == 201
    new_id = created.json()["event"]
    assert new_id != "evt_01"
    again = client.get(f"/api/events/{new_id}/export", **bearer("organizer")).json()
    assert _summary(again) == _summary(bundle)
    assert Event.objects.get(pk=new_id).ledger.count() == 126


def test_a_changed_bundle_is_refused(seeded, client) -> None:
    bundle = client.get("/api/events/evt_01/export", **bearer("organizer")).json()
    tampered = copy.deepcopy(bundle)
    tampered["projects"][0]["versions"][0]["name"] = "Changed after export"
    refused = client.post(
        "/api/event-bundles",
        {"bundle": tampered, "name": "Should fail"},
        content_type="application/json",
        **bearer("organizer"),
    )
    assert refused.status_code == 422
    assert "manifest mismatch" in refused.json()["error"]["message"]
    participant = client.post(
        "/api/event-bundles",
        {"bundle": bundle, "name": "Nope"},
        content_type="application/json",
        **bearer("participant"),
    )
    assert participant.status_code == 403


def test_phase_changes_fire_webhooks(seeded, client, listener) -> None:
    url, received = listener
    client.post(
        "/api/webhooks",
        {"url": url, "events": ["event.phase_changed"], "event_id": "evt_01"},
        content_type="application/json",
        **bearer("organizer"),
    )
    client.post(
        "/api/events/evt_01/phase",
        {"to": "deliberation", "reason": "Judging closed"},
        content_type="application/json",
        **bearer("organizer"),
    )
    _deliver_pending()
    assert [json.loads(raw)["data"]["phase"] for raw, _ in received] == ["deliberation"]
