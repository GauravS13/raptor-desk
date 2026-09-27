from io import StringIO
from pathlib import Path

import pytest
from django.core.management import call_command
from django.test import Client

from apps.events import services as event_services
from apps.events.models import Event, Phase
from apps.judging import feedback
from apps.judging.models import ResultQuery
from apps.seed.demo import SEED_LOGINS
from core import signing
from core.http import ApiError
from core.models import AuditEvent, OutboxMessage
from core.policy import Principal

ORGANIZER = Principal(user_id="organizer", email="organizer@raptor-desk.local")
TOKENS = {login.role: login.token for login in SEED_LOGINS}
PRIYA_PROJECT = "prj_01"  # priya1 is a member of tm_01


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


def _publish(event: Event) -> None:
    event_services.transition(ORGANIZER, event, Phase.DELIBERATION, reason="Judging closed")
    event_services.transition(
        ORGANIZER, event, Phase.PUBLISHED, reason="Publishing for the test.", override=True
    )


def test_reports_open_only_when_results_are_published(event: Event) -> None:
    client = Client()
    response = client.get(f"/api/projects/{PRIYA_PROJECT}/feedback", **bearer("participant"))
    assert response.status_code == 409


def test_the_team_reads_its_report_and_judges_stay_anonymous(event: Event) -> None:
    _publish(event)
    body = Client().get(f"/api/projects/{PRIYA_PROJECT}/feedback", **bearer("participant"))
    assert body.status_code == 200
    report = body.json()
    assert 1 <= report["place"] <= report["of"] == 40
    assert {c["key"] for c in report["criteria"]} == {"functionality", "quality", "innovation"}
    text = body.content.decode()
    assert "jdg_" not in text and "@example.org" not in text


def test_other_people_cannot_read_a_teams_report(event: Event) -> None:
    _publish(event)
    client = Client()
    for role in ("judge_a", "judge_b"):
        response = client.get(f"/api/projects/{PRIYA_PROJECT}/feedback", **bearer(role))
        assert response.status_code == 404
    assert client.get(f"/api/projects/{PRIYA_PROJECT}/feedback").status_code == 401
    organizer = client.get(f"/api/projects/{PRIYA_PROJECT}/feedback", **bearer("organizer"))
    assert organizer.status_code == 200


def test_publishing_emails_every_team_member_once(event: Event) -> None:
    _publish(event)
    mails = OutboxMessage.objects.filter(dedupe_key__startswith="feedback-ready:evt_01:")
    assert mails.count() > 0
    assert all("/feedback" in m.payload["body"] for m in mails)


def test_query_and_answer_round_trip(event: Event) -> None:
    _publish(event)
    client = Client()
    asked = client.post(
        f"/api/projects/{PRIYA_PROJECT}/queries",
        {"message": "One judge reviewed our first version, not the resubmission."},
        content_type="application/json",
        **bearer("participant"),
    )
    assert asked.status_code == 201
    query_id = asked.json()["id"]
    assert client.get("/api/events/evt_01/queries", **bearer("judge_a")).status_code == 403
    answered = client.post(
        f"/api/events/evt_01/queries/{query_id}/answer",
        {"response": "Checked: the judge scored version 2; the scores stand."},
        content_type="application/json",
        **bearer("organizer"),
    )
    assert answered.status_code == 200 and answered.json()["status"] == "answered"
    assert OutboxMessage.objects.filter(dedupe_key=f"query-answered:{query_id}").exists()
    assert AuditEvent.objects.filter(action="judging.result_queried").exists()
    assert AuditEvent.objects.filter(action="judging.query_answered").exists()


def test_queries_need_substance_and_are_limited(event: Event) -> None:
    _publish(event)
    member = event.teams.get(pk="tm_01").members.first()
    priya = Principal(user_id=member.user_id, email=member.user.email)
    with pytest.raises(ApiError) as error:
        feedback.submit_query(priya, PRIYA_PROJECT, "wrong")
    assert error.value.status == 422
    for n in range(feedback.MAX_OPEN_QUERIES):
        feedback.submit_query(priya, PRIYA_PROJECT, f"Query number {n}: a link was broken.")
    with pytest.raises(ApiError) as error:
        feedback.submit_query(priya, PRIYA_PROJECT, "One more query about a broken link.")
    assert error.value.status == 409
    assert ResultQuery.objects.count() == feedback.MAX_OPEN_QUERIES


def test_pages(event: Event) -> None:
    _publish(event)
    client = Client()
    client.post("/login", {"email": "priya1@example.org", "password": "raptor-demo-2026"})
    page = client.get(f"/projects/{PRIYA_PROJECT}/feedback").content.decode()
    assert "Feedback report" in page and "Something factually wrong?" in page
    project_page = client.get(f"/projects/{PRIYA_PROJECT}").content.decode()
    assert f'href="/projects/{PRIYA_PROJECT}/feedback"' in project_page
