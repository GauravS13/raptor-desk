from io import StringIO
from pathlib import Path

import pytest
from django.core.management import call_command
from django.db import IntegrityError, transaction
from django.test import Client

from apps.events.models import Event
from apps.judging import ledger, pairwise
from apps.judging.models import Assignment, Conflict, PairwiseComparison, Review
from apps.seed.demo import SEED_LOGINS
from core import signing
from core.http import ApiError
from core.models import AuditEvent
from core.policy import Principal

TOKENS = {login.role: login.token for login in SEED_LOGINS}
JUDGE_A = Principal(user_id="jdg_26", email="jonas.vogel@example.org")


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


# --- Pairwise mode ------------------------------------------------------------------------


def test_a_judge_is_offered_pairs_of_their_own_projects(event: Event) -> None:
    found = pairwise.next_for(JUDGE_A, event)
    assert found is not None
    mine = set(
        Assignment.objects.filter(event=event, judge_id="jdg_26").values_list(
            "project_id", flat=True
        )
    )
    assert {found.a.pk, found.b.pk} <= mine
    assert found.possible == len(mine) * (len(mine) - 1) // 2


def test_comparisons_are_recorded_once_ledgered_and_ranked(event: Event) -> None:
    before = ledger.count(event)
    seen = []
    while (found := pairwise.next_for(JUDGE_A, event)) is not None:
        pairwise.record(JUDGE_A, event, found.a.pk, found.b.pk, "a")
        seen.append((found.a.pk, found.b.pk))
    assert len(seen) == PairwiseComparison.objects.filter(judge_id="jdg_26").count() > 0
    assert ledger.count(event) == before + len(seen)
    assert ledger.verify(event).valid
    with pytest.raises(ApiError) as error:
        pairwise.record(JUDGE_A, event, seen[0][1], seen[0][0], "b")
    assert error.value.status == 409
    table = pairwise.table(event)
    assert table.total == len(seen) and table.standings[0].rank == 1


def test_the_order_shown_is_kept_when_stored(event: Event) -> None:
    found = pairwise.next_for(JUDGE_A, event)
    high, low = max(found.a.pk, found.b.pk), min(found.a.pk, found.b.pk)
    stored = pairwise.record(JUDGE_A, event, high, low, "a")  # "the first shown is stronger"
    assert (stored.project_a_id, stored.project_b_id, stored.outcome) == (low, high, "b")


def test_only_assigned_projects_can_be_compared(event: Event) -> None:
    mine = set(
        Assignment.objects.filter(event=event, judge_id="jdg_26").values_list(
            "project_id", flat=True
        )
    )
    other = event.projects.exclude(pk__in=mine).first()
    with pytest.raises(ApiError) as error:
        pairwise.record(JUDGE_A, event, sorted(mine)[0], other.pk, "a")
    assert error.value.status == 403


def test_comparisons_are_append_only(event: Event) -> None:
    found = pairwise.next_for(JUDGE_A, event)
    item = pairwise.record(JUDGE_A, event, found.a.pk, found.b.pk, "tie")
    with pytest.raises(IntegrityError), transaction.atomic():
        PairwiseComparison.objects.filter(pk=item.pk).update(outcome="a")


def test_pairwise_api_and_isolation(event: Event) -> None:
    client = Client()
    pair = client.get("/api/events/evt_01/pairwise/next", **bearer("judge_a")).json()
    created = client.post(
        "/api/events/evt_01/pairwise",
        {"a": pair["a"]["id"], "b": pair["b"]["id"], "outcome": "b"},
        content_type="application/json",
        **bearer("judge_a"),
    )
    assert created.status_code == 201
    assert (
        client.get("/api/events/evt_01/pairwise/next", **bearer("participant")).status_code == 403
    )
    assert (
        client.get("/api/events/evt_01/pairwise/standings", **bearer("judge_a")).status_code == 403
    )
    standings = client.get("/api/events/evt_01/pairwise/standings", **bearer("organizer")).json()
    assert standings["comparisons"] == 1


def test_pairwise_page_with_keyboard_buttons(event: Event) -> None:
    client = Client()
    client.post("/login", {"email": "jonas.vogel@example.org", "password": "raptor-demo-2026"})
    page = client.get("/judge/events/evt_01/pairwise").content.decode()
    assert "Which is stronger?" in page and 'data-key="ArrowLeft"' in page


# --- Recusal -------------------------------------------------------------------------------


def _open_assignment() -> Assignment:
    """An assignment still in progress: active, with only a draft review."""
    item = Assignment.objects.filter(event_id="evt_01", judge_id="jdg_26").order_by("pk").first()
    Review.objects.filter(assignment=item).update(status="draft")
    Assignment.objects.filter(pk=item.pk).update(status="active")
    item.refresh_from_db()
    return item


def test_one_click_recusal_records_withdraws_and_proposes_a_replacement(event: Event) -> None:
    from apps.judging import services

    item = _open_assignment()
    result = services.recuse(JUDGE_A, item.pk, "I mentor this team")
    item.refresh_from_db()
    assert item.status == "withdrawn"
    assert Conflict.objects.filter(judge_id="jdg_26", team_id=item.project.team_id).exists()
    assert all(a.status == "proposed" and a.judge_id != "jdg_26" for a in result.replacements)
    assert AuditEvent.objects.filter(action="judging.recused").exists()


def test_recusal_needs_a_reason_and_no_submitted_review(event: Event) -> None:
    from apps.judging import services

    submitted = Assignment.objects.filter(
        event=event, judge_id="jdg_26", reviews__status="submitted"
    ).first()
    with pytest.raises(ApiError) as error:
        services.recuse(JUDGE_A, submitted.pk, "I mentor this team")
    assert error.value.status == 409
    with pytest.raises(ApiError) as error:
        services.recuse(JUDGE_A, _open_assignment().pk, "no")
    assert error.value.status == 422


def test_recusal_api_is_for_the_assigned_judge_only(event: Event) -> None:
    item = _open_assignment()
    client = Client()
    other = client.post(
        f"/api/judge/assignments/{item.pk}/recuse",
        {"reason": "Not my assignment"},
        content_type="application/json",
        **bearer("judge_b"),
    )
    assert other.status_code == 404
    mine = client.post(
        f"/api/judge/assignments/{item.pk}/recuse",
        {"reason": "I mentor this team"},
        content_type="application/json",
        **bearer("judge_a"),
    )
    assert mine.status_code == 200 and mine.json()["withdrawn"] >= 1


# --- Autosave ------------------------------------------------------------------------------


def test_autosave_keeps_a_draft_and_never_touches_a_submitted_review(event: Event) -> None:
    client = Client()
    client.post("/login", {"email": "jonas.vogel@example.org", "password": "raptor-demo-2026"})
    item = _open_assignment()
    saved = client.post(
        f"/judge/review/{item.pk}/autosave",
        {"c_functionality": "4", "comment": "Autosaved thought"},
    )
    assert saved.status_code == 200 and saved.json()["saved"] is True
    review = Review.objects.filter(assignment=item).latest("started_at")
    assert review.comment == "Autosaved thought" and review.status == "draft"

    submitted = Assignment.objects.filter(judge_id="jdg_26", reviews__status="submitted").first()
    refused = client.post(f"/judge/review/{submitted.pk}/autosave", {"comment": "overwrite?"})
    assert refused.status_code == 409
    other = Assignment.objects.exclude(judge_id="jdg_26").first()
    assert client.post(f"/judge/review/{other.pk}/autosave", {"comment": "x"}).status_code == 404
