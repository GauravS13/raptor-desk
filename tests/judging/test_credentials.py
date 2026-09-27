import copy
from io import StringIO
from pathlib import Path

import pytest
from django.core.management import call_command
from django.db import IntegrityError, transaction
from django.test import Client

from apps.events import services as event_services
from apps.events.models import Event, Phase
from apps.judging import credentials
from apps.judging.models import Credential, ScoreEvent
from apps.seed.demo import SEED_LOGINS
from core import signing
from core.models import OutboxMessage
from core.policy import Principal

ORGANIZER = Principal(user_id="organizer", email="organizer@raptor-desk.local")
TOKENS = {login.role: login.token for login in SEED_LOGINS}


def bearer(role: str) -> dict[str, str]:
    return {"HTTP_AUTHORIZATION": f"Bearer {TOKENS[role]}"}


@pytest.fixture
def event(db, settings, tmp_path) -> Event:
    settings.DATA_DIR = tmp_path
    signing.signing_key.cache_clear()
    fixtures = Path(settings.REPO_DIR) / "data" / "fixtures.json"
    call_command("seed", fixtures=str(fixtures), stdout=StringIO())
    yield Event.objects.get(pk="evt_01")
    signing.signing_key.cache_clear()


def _close_judging(event: Event) -> None:
    event_services.transition(ORGANIZER, event, Phase.DELIBERATION, reason="Judging closed")


def _publish(event: Event) -> None:
    _close_judging(event)
    event_services.transition(
        ORGANIZER, event, Phase.PUBLISHED, reason="Publishing for the test.", override=True
    )


def test_closing_judging_issues_one_signed_protocol_per_judge(event: Event) -> None:
    _close_judging(event)
    protocols = Credential.objects.filter(event=event, kind="protocol")
    assert protocols.count() == 30
    mine = protocols.get(user_id="jdg_26")
    assert mine.payload["number"] == mine.label
    assert mine.payload["count"] == len(mine.payload["reviews"]) > 0
    for row in mine.payload["reviews"]:
        entry = ScoreEvent.objects.get(event=event, seq=row["ledger_seq"])
        assert entry.entry_hash == row["ledger_entry_hash"]
        assert entry.payload["scores"] == row["scores"]
    assert credentials.verify(credentials.signed_document(mine))
    assert OutboxMessage.objects.filter(dedupe_key=f"credential:{mine.pk}").exists()


def test_a_protocol_is_private_to_its_judge_and_the_organizers(event: Event) -> None:
    _close_judging(event)
    code = Credential.objects.get(event=event, kind="protocol", user_id="jdg_26").code
    client = Client()
    assert client.get(f"/api/protocols/{code}", **bearer("judge_a")).status_code == 200
    assert client.get(f"/api/protocols/{code}", **bearer("organizer")).status_code == 200
    assert client.get(f"/api/protocols/{code}", **bearer("judge_b")).status_code == 404
    assert client.get(f"/api/protocols/{code}", **bearer("participant")).status_code == 404
    assert client.get(f"/api/verify/{code}").status_code == 404  # never public


def test_publishing_issues_certificates_and_records_without_scores(event: Event) -> None:
    _publish(event)
    certificates = Credential.objects.filter(event=event, kind="certificate")
    records = Credential.objects.filter(event=event, kind="participation")
    assert certificates.count() == 30
    assert records.exists()
    for item in [*certificates[:3], *records[:3]]:
        text = str(item.payload)
        assert "scores" not in text and "place" not in text
    client = Client()
    record = records.first()
    body = client.get(f"/api/verify/{record.code}").json()
    assert body["valid"] and body["payload"]["project"]["id"]
    page = client.get(f"/verify/{record.code}").content.decode()
    assert "Genuine" in page and record.payload["person"]["name"] in page


def test_codes_are_not_guessable_from_numbers(event: Event) -> None:
    _publish(event)
    item = Credential.objects.filter(kind="certificate").first()
    assert item.code != item.label and len(item.code) >= 12
    assert Client().get(f"/verify/{item.label}").status_code == 404


def test_a_forged_certificate_is_rejected(event: Event) -> None:
    _publish(event)
    document = credentials.signed_document(Credential.objects.filter(kind="certificate").first())
    forged = copy.deepcopy(document)
    forged["payload"]["projects_reviewed"] = 99
    assert not credentials.verify(forged)
    forged["payload_hash"] = signing.payload_hash(forged["payload"])
    assert not credentials.verify(forged)


def test_documents_are_append_only_and_issued_once(event: Event) -> None:
    _publish(event)
    before = Credential.objects.count()
    credentials.issue_public_records(event)
    assert Credential.objects.count() == before
    item = Credential.objects.first()
    with pytest.raises(IntegrityError), transaction.atomic():
        Credential.objects.filter(pk=item.pk).update(payload_hash="0" * 64)


def test_judges_find_their_documents_in_their_queue(event: Event) -> None:
    _publish(event)
    client = Client()
    listed = client.get("/api/me/credentials", **bearer("judge_a")).json()
    assert {c["kind"] for c in listed} == {"protocol", "certificate"}
    client.post("/login", {"email": "jonas.vogel@example.org", "password": "raptor-demo-2026"})
    page = client.get("/judge").content.decode()
    assert "Your signed documents" in page
    protocol = next(c for c in listed if c["kind"] == "protocol")
    assert protocol["number"] in client.get(f"/judge/protocols/{protocol['code']}").content.decode()


def test_anyone_can_check_a_pasted_document(event: Event) -> None:
    import json

    _publish(event)
    document = credentials.signed_document(Credential.objects.filter(kind="certificate").first())
    client = Client()
    genuine = client.post("/api/verify", document, content_type="application/json").json()
    assert genuine["valid"] is True and genuine["kind"] == "raptor-desk/judging-certificate"
    ok = client.post("/verify", {"document": json.dumps(document)}).content.decode()
    assert "Genuine." in ok
    document["payload"]["projects_reviewed"] = 99
    bad = client.post("/verify", {"document": json.dumps(document)}).content.decode()
    assert "Not genuine." in bad and "changed" in bad
    assert "not JSON" in client.post("/verify", {"document": "{nope"}).content.decode()
    api = client.post("/api/verify", document, content_type="application/json").json()
    assert api["valid"] is False
    code = Credential.objects.filter(kind="certificate").first().code
    assert client.get(f"/verify?code={code}")["Location"] == f"/verify/{code}"
