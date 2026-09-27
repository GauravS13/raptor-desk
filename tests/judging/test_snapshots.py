import copy
from io import StringIO
from pathlib import Path

import pytest
from django.core.management import call_command
from django.db import IntegrityError, transaction
from django.test import Client

from apps.events import services as event_services
from apps.events.models import Event, Phase
from apps.judging import deliberation, snapshots
from apps.judging.models import ResultsSnapshot
from apps.seed.demo import SEED_LOGINS
from core import signing
from core.http import ApiError
from core.models import AuditEvent
from core.policy import Principal

ORGANIZER = Principal(user_id="organizer", email="organizer@raptor-desk.local")
TOKENS = {login.role: login.token for login in SEED_LOGINS}


@pytest.fixture
def event(db, settings, tmp_path) -> Event:
    settings.DATA_DIR = tmp_path
    signing.signing_key.cache_clear()
    fixtures = Path(settings.REPO_DIR) / "data" / "fixtures.json"
    call_command("seed", fixtures=str(fixtures), stdout=StringIO())
    event = Event.objects.get(pk="evt_01")
    yield event
    signing.signing_key.cache_clear()


def _deliberate(event: Event) -> Event:
    event_services.transition(ORGANIZER, event, Phase.DELIBERATION, reason="Judging closed")
    return event


def test_results_can_only_be_frozen_in_deliberation(event: Event) -> None:
    with pytest.raises(ApiError) as error:
        snapshots.freeze(ORGANIZER, event)
    assert error.value.status == 409


def test_a_frozen_snapshot_is_signed_and_carries_the_final_order(event: Event) -> None:
    _deliberate(event)
    deliberation.record_decision(
        ORGANIZER,
        event,
        kind="place_above",
        project_id="prj_16",
        other_id="prj_33",
        rationale="The panel prefers the working demo.",
    )
    snap = snapshots.freeze(ORGANIZER, event)
    stored = ResultsSnapshot.objects.get(pk=snap.pk)
    document = snapshots.signed_document(stored)

    assert snapshots.verify(document)
    assert signing.payload_hash(stored.payload) == stored.payload_hash  # survives the database
    ranking = [row["project"] for row in stored.payload["ranking"]]
    assert ranking == deliberation.board(event).final_order
    assert stored.payload["decisions"][0]["rationale"] == "The panel prefers the working demo."
    assert stored.payload["reviews"] == 123
    assert AuditEvent.objects.filter(action="judging.results_frozen").exists()


def test_a_tampered_copy_fails_verification(event: Event) -> None:
    _deliberate(event)
    document = snapshots.signed_document(snapshots.freeze(ORGANIZER, event))
    forged = copy.deepcopy(document)
    forged["payload"]["ranking"][0], forged["payload"]["ranking"][1] = (
        forged["payload"]["ranking"][1],
        forged["payload"]["ranking"][0],
    )
    assert not snapshots.verify(forged)
    forged["payload_hash"] = signing.payload_hash(forged["payload"])
    assert not snapshots.verify(forged)  # a matching hash does not fix the signature


def test_snapshots_are_append_only_and_numbered(event: Event) -> None:
    _deliberate(event)
    first = snapshots.freeze(ORGANIZER, event)
    second = snapshots.freeze(ORGANIZER, event)
    assert (first.number, second.number) == (1, 2)
    with pytest.raises(IntegrityError), transaction.atomic():
        ResultsSnapshot.objects.filter(pk=first.pk).update(payload_hash="0" * 64)
    with pytest.raises(IntegrityError), transaction.atomic():
        ResultsSnapshot.objects.filter(pk=first.pk).delete()


def test_a_new_decision_makes_the_snapshot_out_of_date(event: Event) -> None:
    _deliberate(event)
    snap = snapshots.freeze(ORGANIZER, event)
    assert snapshots.is_current(event, snap)
    deliberation.record_decision(
        ORGANIZER, event, kind="confirm", project_id="prj_25", rationale="Agreed by the panel."
    )
    assert not snapshots.is_current(event, snap)


def test_api_and_download(event: Event) -> None:
    _deliberate(event)
    client = Client()
    org = {"HTTP_AUTHORIZATION": f"Bearer {TOKENS['organizer']}"}
    created = client.post("/api/events/evt_01/results/snapshots", **org)
    assert created.status_code == 201 and created.json()["number"] == 1
    document = client.get("/api/events/evt_01/results/snapshots/1", **org).json()
    assert snapshots.verify(document)
    judge = {"HTTP_AUTHORIZATION": f"Bearer {TOKENS['judge_a']}"}
    assert client.get("/api/events/evt_01/results/snapshots/1", **judge).status_code == 403

    client.post(
        "/login",
        {"email": "organizer@raptor-desk.local", "password": "raptor-demo-2026", "next": "/"},
    )
    page = client.get("/o/events/evt_01/deliberation").content.decode()
    assert "Freeze and sign" in page and "current" in page
    download = client.get("/o/events/evt_01/results/snapshots/1.json")
    assert download["Content-Disposition"].startswith("attachment")
    assert snapshots.verify(download.json())
