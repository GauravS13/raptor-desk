from io import StringIO
from pathlib import Path

import pytest
from django.conf import settings
from django.core.management import call_command
from django.test import Client

from apps.events.models import Event
from apps.judging import close_calls, services
from apps.judging.models import Assignment, Review
from apps.seed.demo import DOGFOOD_EVENT_ID, SEED_LOGINS
from core.http import ApiError
from core.models import AuditEvent, OutboxMessage
from core.policy import Principal

TOKENS = {login.role: login.token for login in SEED_LOGINS}
ORGANIZER = Principal(user_id="organizer", email="organizer@raptor-desk.local")


@pytest.fixture
def event(db) -> Event:
    fixtures = Path(settings.REPO_DIR) / "data" / "fixtures.json"
    call_command("seed", fixtures=str(fixtures), stdout=StringIO())
    return Event.objects.get(pk="evt_01")


def bearer(role: str) -> dict[str, str]:
    return {"HTTP_AUTHORIZATION": f"Bearer {TOKENS[role]}"}


def test_doubt_finds_the_close_calls_on_the_prize_places(event: Event) -> None:
    found = close_calls.report(event)
    uncertain = {c.project.id for c in found.close_calls}
    assert {"prj_37", "prj_25", "prj_33"} <= uncertain  # places 3 to 5
    assert all(0.2 < c.p < 0.8 for c in found.close_calls)
    # Most uncertain first.
    distances = [abs(c.p - 0.5) for c in found.close_calls]
    assert distances == sorted(distances)


def test_ask_respects_every_hard_constraint(event: Event) -> None:
    found = close_calls.report(event, budget=20)
    assert 0 < len(found.proposals) <= 20
    tracks = services.tracks_of(event)
    conflicts = services.conflict_pairs(event)
    for proposal in found.proposals:
        project = event.projects.get(pk=proposal.project)
        assert not Assignment.objects.filter(
            judge_id=proposal.judge, project_id=proposal.project
        ).exists()
        assert proposal.judge != "jdg_07"  # the flat-liner's reviews carry no signal
        assert not tracks[proposal.judge] or project.track_id in tracks[proposal.judge]
        assert (proposal.judge, project.team_id) not in conflicts
    assert len({(p.judge, p.project) for p in found.proposals}) == len(found.proposals)


def test_every_close_call_says_what_happens_to_it(event: Event) -> None:
    found = close_calls.report(event, budget=6)
    statuses = found.statuses
    assert set(statuses) == {c.project.id for c in found.close_calls}
    for project in found.unaskable:
        assert statuses[project] == "no eligible judge left"
    for proposal in found.proposals:
        assert statuses[proposal.project].endswith("review(s) proposed")


def test_approving_assigns_emails_audits_and_then_waits(event: Event) -> None:
    proposals = close_calls.report(event, budget=4).proposals
    emails_before = OutboxMessage.objects.count()
    created = close_calls.approve(ORGANIZER, event, budget=4)

    assert [(a.judge_id, a.project_id) for a in created] == [
        (p.judge, p.project) for p in proposals
    ]
    assert {a.source for a in created} == {"close_call"}
    assert OutboxMessage.objects.count() > emails_before
    assert AuditEvent.objects.filter(action="judging.close_calls_asked").exists()

    after = close_calls.report(event, budget=4)
    asked = {a.project_id for a in created}
    assert asked <= set(after.waiting)
    assert not asked & {p.project for p in after.proposals}


def test_a_submitted_review_ends_the_wait(event: Event) -> None:
    created = close_calls.approve(ORGANIZER, event, budget=2)
    item = created[0]
    judge = Principal(user_id=item.judge_id, email="")
    values = {c.key: 4 for c in event.criteria.all()}
    services.save_review(
        judge, item, values, comment="Clear demo", improvement="Add tests", submit=True
    )
    assert Review.objects.filter(assignment=item, status="submitted").exists()
    assert item.judge_id not in close_calls.outstanding(event).get(item.project_id, [])


def test_only_current_proposals_can_be_approved(event: Event) -> None:
    with pytest.raises(ApiError) as error:
        close_calls.approve(ORGANIZER, event, pairs=[("jdg_07", "prj_37")])
    assert error.value.status == 422


def test_asking_needs_the_judging_phase(event: Event) -> None:
    dogfood = Event.objects.get(pk=DOGFOOD_EVENT_ID)
    with pytest.raises(ApiError) as error:
        close_calls.approve(ORGANIZER, dogfood)
    assert error.value.status == 409


def test_api_is_for_organizers_only(event: Event) -> None:
    client = Client()
    ok = client.get("/api/events/evt_01/close-calls?budget=4", **bearer("organizer"))
    assert ok.status_code == 200
    body = ok.json()
    assert body["budget"] == 4 and len(body["proposals"]) <= 4
    assert client.get("/api/events/evt_01/close-calls", **bearer("judge_a")).status_code == 403
    assert client.get("/api/events/evt_01/close-calls").status_code == 401

    asked = client.post(
        "/api/events/evt_01/close-calls/ask",
        {"budget": 2},
        content_type="application/json",
        **bearer("organizer"),
    )
    assert asked.status_code == 201
    assert {a["source"] for a in asked.json()} == {"close_call"}


def test_results_page_offers_the_ask(event: Event) -> None:
    client = Client()
    client.post(
        "/login",
        {"email": "organizer@raptor-desk.local", "password": "raptor-demo-2026", "next": "/o/"},
    )
    page = client.get("/o/events/evt_01/results?budget=4").content.decode()
    assert "Close calls" in page and "Ask these judges" in page
    response = client.post("/o/events/evt_01/close-calls/ask", {"budget": "4"})
    assert response.status_code == 302
    assert Assignment.objects.filter(event=event, source="close_call").count() == 4
