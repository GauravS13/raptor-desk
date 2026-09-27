from io import StringIO
from pathlib import Path

import pytest
from django.core.management import call_command
from django.db import IntegrityError, connection, transaction
from django.test import Client

from apps.events.models import Event
from apps.judging import ledger, services
from apps.judging.models import Assignment, Review, ScoreEvent, ScoreItem
from apps.seed.demo import SEED_LOGINS
from core import signing
from core.policy import Principal

TOKENS = {login.role: login.token for login in SEED_LOGINS}


@pytest.fixture
def event(db, settings, tmp_path) -> Event:
    settings.DATA_DIR = tmp_path
    signing.signing_key.cache_clear()
    fixtures = Path(settings.REPO_DIR) / "data" / "fixtures.json"
    call_command("seed", fixtures=str(fixtures), stdout=StringIO())
    yield Event.objects.get(pk="evt_01")
    signing.signing_key.cache_clear()


def test_every_imported_review_is_in_a_valid_chain(event: Event) -> None:
    result = ledger.verify(event)
    assert result.valid, result
    assert result.entries == Review.objects.filter(event=event, status="submitted").count() == 126
    first = ScoreEvent.objects.get(event=event, seq=1)
    assert first.prev_hash == signing.GENESIS_HASH
    assert "comment" not in first.payload  # feedback is hashed, not copied


def test_submitting_and_amending_append_entries(event: Event) -> None:
    item = Assignment.objects.filter(event=event, judge_id="jdg_26").first()
    judge = Principal(user_id="jdg_26", email="")
    scores = {c.key: 3 for c in event.criteria.all()}
    before = ledger.count(event)
    services.save_review(judge, item, scores, comment="First look", submit=True)
    services.save_review(judge, item, {**scores, "quality": 5}, comment="Second look", submit=True)
    entries = list(ScoreEvent.objects.filter(event=event, seq__gt=before))
    assert [e.payload["kind"] for e in entries] == ["amended", "amended"]
    assert entries[-1].payload["scores"]["quality"] == 5
    assert ledger.verify(event).valid


def test_a_score_edited_directly_in_the_database_is_found(event: Event) -> None:
    item = ScoreItem.objects.filter(review__event=event).order_by("review__submitted_at").first()
    with connection.cursor() as cursor:
        cursor.execute(
            "UPDATE judging_scoreitem SET value = %s WHERE id = %s",
            [5 if item.value != 5 else 1, item.pk],
        )
    result = ledger.verify(event)
    assert not result.valid
    entry = ScoreEvent.objects.filter(review_id=item.review_id).order_by("-seq").first()
    assert result.first_bad_seq == entry.seq
    assert "changed outside the portal" in result.problem


def test_feedback_edited_directly_is_found(event: Event) -> None:
    review = Review.objects.filter(event=event).exclude(comment="").first()
    with connection.cursor() as cursor:
        sql = "UPDATE judging_review SET comment = %s WHERE id = %s"
        cursor.execute(sql, ["Rewritten", review.pk])
    assert "feedback" in ledger.verify(event).problem


def test_the_ledger_itself_cannot_be_edited(event: Event) -> None:
    with pytest.raises(IntegrityError), transaction.atomic():
        ScoreEvent.objects.filter(event=event, seq=5).update(payload_hash="0" * 64)
    with pytest.raises(IntegrityError), transaction.atomic():
        ScoreEvent.objects.filter(event=event, seq=5).delete()


def test_api_for_organizers(event: Event) -> None:
    client = Client()
    org = {"HTTP_AUTHORIZATION": f"Bearer {TOKENS['organizer']}"}
    body = client.get("/api/events/evt_01/ledger/verify", **org).json()
    assert body["valid"] and body["entries"] == 126
    exported = client.get("/api/events/evt_01/ledger", **org).json()
    assert len(exported["entries"]) == 126 and exported["public_key"]
    judge = {"HTTP_AUTHORIZATION": f"Bearer {TOKENS['judge_a']}"}
    assert client.get("/api/events/evt_01/ledger", **judge).status_code == 403
