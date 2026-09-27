from io import StringIO
from pathlib import Path

import pytest
from django.core.management import call_command
from django.db import IntegrityError, transaction
from django.test import Client

from apps.events import services as event_services
from apps.events.models import Event, Phase
from apps.judging import deliberation, snapshots
from apps.judging.models import Publication
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
    event_services.transition(ORGANIZER, event, Phase.DELIBERATION, reason="Judging closed")
    yield event
    signing.signing_key.cache_clear()


def _decide_everything(event: Event) -> None:
    for row in deliberation.board(event).undecided:
        deliberation.record_decision(
            ORGANIZER,
            event,
            kind="confirm",
            project_id=row.project_id,
            rationale="The panel agrees with the computed place.",
        )


def _publish(event: Event, **kwargs):
    return event_services.transition(ORGANIZER, event, Phase.PUBLISHED, **kwargs)


def test_publishing_is_blocked_until_results_are_frozen_and_decided(event: Event) -> None:
    with pytest.raises(ApiError) as error:
        _publish(event)
    codes = {c["code"] for c in error.value.details["checks"] if c["level"] == "blocking"}
    assert codes == {"no_snapshot", "undecided_close_calls"}

    _decide_everything(event)
    snapshots.freeze(ORGANIZER, event)
    _publish(event)
    event.refresh_from_db()
    assert event.phase == Phase.PUBLISHED
    publication = Publication.objects.get(event=event)
    assert publication.snapshot.number == 1
    assert AuditEvent.objects.filter(action="judging.results_published").exists()


def test_an_out_of_date_snapshot_blocks_publishing(event: Event) -> None:
    snapshots.freeze(ORGANIZER, event)
    _decide_everything(event)  # decisions made after the freeze
    with pytest.raises(ApiError) as error:
        _publish(event)
    codes = {c["code"] for c in error.value.details["checks"]}
    assert "snapshot_out_of_date" in codes


def test_overriding_without_a_snapshot_still_publishes_a_signed_one(event: Event) -> None:
    _publish(event, reason="Prize ceremony starts now; decisions made verbally.", override=True)
    publication = Publication.objects.get(event=event)
    assert snapshots.verify(snapshots.signed_document(publication.snapshot))


def test_public_results_appear_only_after_publishing(event: Event) -> None:
    client = Client()
    assert client.get("/events/evt_01/results").status_code == 404
    assert client.get("/api/events/evt_01/results/published").status_code == 404

    _decide_everything(event)
    snapshot = snapshots.freeze(ORGANIZER, event)
    _publish(event)

    assert 'href="/events/evt_01/results"' in client.get("/events/evt_01").content.decode()
    page = client.get("/events/evt_01/results")
    assert page.status_code == 200
    html = page.content.decode()
    assert snapshot.payload_hash in html and "How these results were made" in html
    assert "Iron Switch" in html
    document = client.get("/api/events/evt_01/results/published").json()
    assert snapshots.verify(document)

    # Publishing opens the results, not the judges' individual scores.
    judge = {"HTTP_AUTHORIZATION": f"Bearer {TOKENS['judge_b']}"}
    assert client.get("/api/judges/jdg_26/scores", **judge).status_code == 403
    assert client.get("/api/events/evt_01/results", **judge).status_code == 403


def test_a_publication_is_permanent(event: Event) -> None:
    _publish(event, reason="Publishing for the test.", override=True)
    with pytest.raises(IntegrityError), transaction.atomic():
        Publication.objects.filter(event=event).delete()
