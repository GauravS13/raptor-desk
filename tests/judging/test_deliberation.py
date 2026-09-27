from io import StringIO
from pathlib import Path

import pytest
from django.conf import settings
from django.core.management import call_command
from django.db import IntegrityError, transaction
from django.test import Client

from apps.events.models import Event
from apps.judging import deliberation
from apps.judging.models import RankingDecision
from apps.seed.demo import SEED_LOGINS
from core.http import ApiError
from core.models import AuditEvent
from core.policy import Principal

ORGANIZER = Principal(user_id="organizer", email="organizer@raptor-desk.local")
TOKENS = {login.role: login.token for login in SEED_LOGINS}


@pytest.fixture
def event(db) -> Event:
    fixtures = Path(settings.REPO_DIR) / "data" / "fixtures.json"
    call_command("seed", fixtures=str(fixtures), stdout=StringIO())
    return Event.objects.get(pk="evt_01")


def test_board_starts_as_the_computed_order_with_open_close_calls(event: Event) -> None:
    board = deliberation.board(event)
    assert [r.final_rank for r in board.rows] == [r.computed_rank for r in board.rows]
    assert board.final_order[:3] == ["prj_34", "prj_11", "prj_37"]
    assert board.prize_places == 5
    assert {"prj_37", "prj_25", "prj_33"} <= {r.project_id for r in board.undecided}


def test_place_above_moves_one_project_and_is_logged(event: Event) -> None:
    deliberation.record_decision(
        ORGANIZER,
        event,
        kind="place_above",
        project_id="prj_16",
        other_id="prj_33",
        rationale="The panel re-read both; prj_16 ships a working demo.",
    )
    board = deliberation.board(event)
    order = board.final_order
    assert order.index("prj_16") == order.index("prj_33") - 1
    moved = next(r for r in board.rows if r.project_id == "prj_16")
    assert moved.final_rank == 5 and moved.computed_rank == 6
    assert "moved from computed place 6 by decision" in moved.notes
    assert AuditEvent.objects.filter(action="judging.decision_recorded").exists()


def test_confirm_settles_a_close_call(event: Event) -> None:
    deliberation.record_decision(
        ORGANIZER,
        event,
        kind="confirm",
        project_id="prj_25",
        rationale="Two more reviews were not possible; the panel agrees with 4th.",
    )
    assert "prj_25" not in {r.project_id for r in deliberation.board(event).undecided}


def test_every_decision_needs_a_reason_and_a_valid_target(event: Event) -> None:
    cases = [
        {"kind": "confirm", "project_id": "prj_25", "rationale": "ok"},
        {"kind": "place_above", "project_id": "prj_25", "rationale": "long enough reason"},
        {
            "kind": "place_above",
            "project_id": "prj_25",
            "other_id": "prj_25",
            "rationale": "x" * 20,
        },
        {"kind": "promote", "project_id": "prj_25", "rationale": "long enough reason"},
        {"kind": "confirm", "project_id": "prj_nope", "rationale": "long enough reason"},
    ]
    for case in cases:
        with pytest.raises(ApiError) as error:
            deliberation.record_decision(ORGANIZER, event, **case)
        assert error.value.status == 422, case


def test_decisions_are_append_only(event: Event) -> None:
    decision = deliberation.record_decision(
        ORGANIZER, event, kind="confirm", project_id="prj_25", rationale="Agreed by the panel."
    )
    with pytest.raises(IntegrityError), transaction.atomic():
        RankingDecision.objects.filter(pk=decision.pk).update(rationale="rewritten")
    with pytest.raises(IntegrityError), transaction.atomic():
        RankingDecision.objects.filter(pk=decision.pk).delete()


def _row(pid: str, rank: int, score: float, bonus: float) -> deliberation.BoardRow:
    return deliberation.BoardRow(rank, rank, pid, pid, "", score, bonus, 0, 0, {}, [])


def test_the_bonus_tie_break_is_explained() -> None:
    rows = [_row("a", 1, 4.2, 3.0), _row("b", 2, 4.2, 1.0), _row("c", 3, 4.2, 1.0)]
    deliberation.explain_ties(rows)
    assert rows[0].notes == ["exact tie with b broken by bonus points (3 against 1)"]
    assert rows[1].notes == ["exact tie with c, equal bonus: ordered by id; decide it"]
    assert rows[2].notes == []


def test_api_and_page(event: Event) -> None:
    client = Client()
    auth = {"HTTP_AUTHORIZATION": f"Bearer {TOKENS['organizer']}"}
    body = client.get("/api/events/evt_01/deliberation", **auth).json()
    assert body["prize_places"] == 5 and body["rows"][0]["project_id"] == "prj_34"
    created = client.post(
        "/api/events/evt_01/deliberation/decisions",
        {"kind": "confirm", "project_id": "prj_37", "rationale": "Clear winner of the panel vote."},
        content_type="application/json",
        **auth,
    )
    assert created.status_code == 201
    judge = {"HTTP_AUTHORIZATION": f"Bearer {TOKENS['judge_a']}"}
    assert client.get("/api/events/evt_01/deliberation", **judge).status_code == 403

    client.post(
        "/login",
        {"email": "organizer@raptor-desk.local", "password": "raptor-demo-2026", "next": "/"},
    )
    page = client.get("/o/events/evt_01/deliberation").content.decode()
    assert "Decision log" in page and "Clear winner of the panel vote." in page
