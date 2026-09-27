from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from django.db import DatabaseError, IntegrityError, transaction
from django.test import Client

from apps.accounts.models import ApiToken, RoleGrant, User
from apps.events.models import Criterion, Event, PhaseTransition, Track
from core import clock

CLOSE = datetime(2026, 3, 1, 18, 0, tzinfo=UTC)


@pytest.fixture
def event(db) -> Event:
    return Event.objects.create(
        id="evt_01", slug="sample-hack", name="Sample Hack 2026", submissions_close_at=CLOSE
    )


def test_submission_window_follows_the_clock(event: Event) -> None:
    with clock.frozen(CLOSE - timedelta(seconds=1)):
        assert event.submissions_are_open
    with clock.frozen(CLOSE):
        assert not event.submissions_are_open


def test_database_rejects_inverted_submission_window(event: Event) -> None:
    event.submissions_open_at = CLOSE + timedelta(days=1)
    with pytest.raises(IntegrityError), transaction.atomic():
        event.save()


def test_criterion_constraints(event: Event) -> None:
    Criterion.objects.create(event=event, key="quality", label="Quality", weight=Decimal("40"))
    with pytest.raises(IntegrityError), transaction.atomic():
        Criterion.objects.create(event=event, key="quality", label="Duplicate key")
    with pytest.raises(IntegrityError), transaction.atomic():
        Criterion.objects.create(event=event, key="bad", label="Bad", min_score=5, max_score=1)
    with pytest.raises(IntegrityError), transaction.atomic():
        Criterion.objects.create(event=event, key="gate", label="Gate", is_gate=True)


def test_track_names_unique_per_event(event: Event) -> None:
    Track.objects.create(event=event, name="Security")
    other = Event.objects.create(slug="other", name="Other")
    Track.objects.create(event=other, name="Security")
    with pytest.raises(IntegrityError), transaction.atomic():
        Track.objects.create(event=event, name="Security")


def test_phase_history_is_append_only(event: Event) -> None:
    entry = PhaseTransition.objects.create(event=event, from_phase="draft", to_phase="registration")
    with pytest.raises(DatabaseError), transaction.atomic():
        PhaseTransition.objects.filter(pk=entry.pk).update(reason="rewritten")


def test_grants_reach_the_principal_per_event(event: Event) -> None:
    user = User.objects.create_user("jdg26@example.org")
    RoleGrant.objects.create(user=user, event=event, role="judge")
    with pytest.raises(IntegrityError), transaction.atomic():
        RoleGrant.objects.create(user=user, event=event, role="judge")
    _, raw = ApiToken.issue(user, "t")
    body = Client().get("/api/me", HTTP_AUTHORIZATION=f"Bearer {raw}").json()
    assert body["roles"] == {"evt_01": ["judge"]}
