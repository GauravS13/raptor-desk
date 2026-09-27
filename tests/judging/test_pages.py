from io import StringIO
from pathlib import Path

import pytest
from django.conf import settings
from django.core.management import call_command
from django.test import Client

from apps.accounts.models import User
from apps.judging.models import Assignment, Review
from apps.submissions.models import Project
from core.actions import parity_gaps


@pytest.fixture
def seeded(db) -> None:
    fixtures = Path(settings.REPO_DIR) / "data" / "fixtures.json"
    call_command("seed", fixtures=str(fixtures), stdout=StringIO())


def login(email: str) -> Client:
    client = Client()
    client.force_login(User.objects.get(email=email))
    return client


def assign_new_project_to_jonas() -> Assignment:
    reviewed = set(Review.objects.filter(judge_id="jdg_26").values_list("project_id", flat=True))
    project = (
        Project.objects.filter(event_id="evt_01", track_id__in={"trk_01", "trk_03"})
        .exclude(pk__in=reviewed)
        .first()
    )
    return Assignment.objects.create(
        event_id="evt_01", judge_id="jdg_26", project=project, source="batch", status="active"
    )


def test_parity_holds_for_judging_pages() -> None:
    assert parity_gaps() == []


def test_judge_queue_and_review_form(seeded: None) -> None:
    item = assign_new_project_to_jonas()
    judge = login("jonas.vogel@example.org")
    queue = judge.get("/judge")
    assert queue.status_code == 200
    assert f"/judge/review/{item.pk}".encode() in queue.content

    page = judge.get(f"/judge/review/{item.pk}")
    assert page.status_code == 200
    assert b'name="c_functionality"' in page.content

    draft = judge.post(f"/judge/review/{item.pk}", {"c_quality": "4", "save": "1"})
    assert draft.status_code == 302

    missing = judge.post(f"/judge/review/{item.pk}", {"c_quality": "4", "submit": "1"})
    assert missing.status_code == 422
    assert b"Score every criterion" in missing.content

    done = judge.post(
        f"/judge/review/{item.pk}",
        {
            "c_functionality": "5",
            "c_quality": "4",
            "c_innovation": "3",
            "comment": "Clear and working.",
            "submit": "1",
            "active_seconds": "95",
        },
    )
    assert done.status_code == 302
    review = Review.objects.get(assignment=item)
    assert review.status == "submitted"
    assert review.active_seconds == 95


def test_other_judges_cannot_open_the_review_page(seeded: None) -> None:
    item = assign_new_project_to_jonas()
    assert login("diego.herrera@example.org").get(f"/judge/review/{item.pk}").status_code == 404


def test_organizer_judging_page_and_exports(seeded: None) -> None:
    organizer = login("organizer@raptor-desk.local")
    page = organizer.get("/o/events/evt_01/judging")
    assert page.status_code == 200
    assert b"Review coverage" in page.content
    assert organizer.post("/o/events/evt_01/judging/plan").status_code == 302
    assert Assignment.objects.filter(event_id="evt_01", status="proposed").exists()
    assert organizer.post("/o/events/evt_01/judging/publish").status_code == 302
    assert not Assignment.objects.filter(event_id="evt_01", status="proposed").exists()

    exports = organizer.get("/o/events/evt_01/exports")
    assert b"results.csv" in exports.content
    csv = organizer.get("/o/events/evt_01/exports/results.csv")
    assert csv.status_code == 200
    assert "," in csv.content.decode().splitlines()[0]


def test_judges_and_participants_cannot_open_organizer_pages(seeded: None) -> None:
    for email in ("jonas.vogel@example.org", "priya1@example.org"):
        client = login(email)
        assert client.get("/o/events/evt_01/judging").status_code == 403
        assert client.get("/o/events/evt_01/exports/results.csv").status_code == 403
