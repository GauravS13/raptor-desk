from io import StringIO
from pathlib import Path
from typing import Any

import pytest
from django.conf import settings
from django.core.management import call_command
from django.test import Client

from apps.events.models import Event
from apps.judging.models import Assignment, Review
from apps.seed.demo import SEED_LOGINS
from apps.submissions.models import Project
from core.models import AuditEvent

TOKENS = {login.role: login.token for login in SEED_LOGINS}


@pytest.fixture
def seeded(db) -> None:
    fixtures = Path(settings.REPO_DIR) / "data" / "fixtures.json"
    call_command("seed", fixtures=str(fixtures), stdout=StringIO())


def call(method: str, path: str, role: str | None, body: dict[str, Any] | None = None) -> Any:
    headers = {"HTTP_AUTHORIZATION": f"Bearer {TOKENS[role]}"} if role else {}
    client = Client()
    if method == "get":
        return client.get(path, **headers)
    return getattr(client, method)(path, body or {}, content_type="application/json", **headers)


def unreviewed_project_for(judge_id: str, track_ids: set[str]) -> Project:
    reviewed = set(Review.objects.filter(judge_id=judge_id).values_list("project_id", flat=True))
    return (
        Project.objects.filter(event_id="evt_01", track_id__in=track_ids)
        .exclude(pk__in=reviewed)
        .order_by("pk")
        .first()
    )


def test_plan_tops_up_only_under_reviewed_projects_within_tracks(seeded: None) -> None:
    response = call("post", "/api/events/evt_01/assignments/plan", "organizer")
    assert response.status_code == 200
    body = response.json()
    under = {"prj_10", "prj_15", "prj_18", "prj_19", "prj_24", "prj_29", "prj_39", "prj_40"}
    proposed = {p["project_id"] for p in body["proposed"]}
    assert proposed <= under
    for proposal in body["proposed"]:
        project = Project.objects.get(pk=proposal["project_id"])
        judge_tracks = set(
            Event.objects.get(pk="evt_01")
            .judge_tracks.filter(judge_id=proposal["judge_id"])
            .values_list("track_id", flat=True)
        )
        assert project.track_id in judge_tracks
    # Proposals are invisible to judges until published.
    judge = proposed and body["proposed"][0]["judge_id"]
    assert Assignment.objects.filter(judge_id=judge, status="proposed").exists()
    published = call("post", "/api/events/evt_01/assignments/publish", "organizer").json()
    assert published["published"] == len(body["proposed"])


def test_judge_reviews_an_assigned_project_end_to_end(seeded: None) -> None:
    project = unreviewed_project_for("jdg_26", {"trk_01", "trk_03"})
    assigned = call(
        "post",
        "/api/events/evt_01/assignments",
        "organizer",
        {"judge_id": "jdg_26", "project_ids": [project.pk]},
    )
    assert assigned.status_code == 201
    assignment_id = assigned.json()[0]["id"]

    queue = call("get", "/api/judge/queue?event=evt_01", "judge_a").json()
    assert assignment_id in {item["assignment_id"] for item in queue}

    detail = call("get", f"/api/judge/assignments/{assignment_id}", "judge_a").json()
    assert [c["key"] for c in detail["criteria"]] == ["functionality", "quality", "innovation"]

    path = f"/api/judge/assignments/{assignment_id}/review"
    draft = call("put", path, "judge_a", {"scores": {"functionality": 4}})
    assert draft.json()["review_status"] == "draft"

    no_feedback = call(
        "put",
        path,
        "judge_a",
        {"scores": {"functionality": 4, "quality": 3, "innovation": 5}, "submit": True},
    )
    assert no_feedback.status_code == 422

    incomplete = call(
        "put",
        path,
        "judge_a",
        {"scores": {"functionality": 4}, "comment": "Solid.", "submit": True},
    )
    assert incomplete.status_code == 422

    out_of_range = call("put", path, "judge_a", {"scores": {"quality": 9}})
    assert out_of_range.status_code == 422

    done = call(
        "put",
        path,
        "judge_a",
        {
            "scores": {"functionality": 4, "quality": 3, "innovation": 5},
            "comment": "Runs clean.",
            "improvement": "Add tests.",
            "submit": True,
        },
    )
    assert done.status_code == 200
    assert done.json()["review_status"] == "submitted"
    assert Assignment.objects.get(pk=assignment_id).status == "done"
    assert AuditEvent.objects.filter(action="review.submitted").exists()


def test_a_judge_cannot_open_another_judges_assignment(seeded: None) -> None:
    project = unreviewed_project_for("jdg_26", {"trk_01", "trk_03"})
    assignment_id = call(
        "post",
        "/api/events/evt_01/assignments",
        "organizer",
        {"judge_id": "jdg_26", "project_ids": [project.pk]},
    ).json()[0]["id"]
    assert call("get", f"/api/judge/assignments/{assignment_id}", "judge_b").status_code == 404
    assert (
        call(
            "put",
            f"/api/judge/assignments/{assignment_id}/review",
            "judge_b",
            {"scores": {"quality": 5}},
        ).status_code
        == 404
    )
    assert call("get", f"/api/judge/assignments/{assignment_id}", "participant").status_code == 403


def test_assignment_respects_tracks(seeded: None) -> None:
    other_track = (
        Project.objects.filter(event_id="evt_01").exclude(track_id__in={"trk_01", "trk_03"}).first()
    )
    response = call(
        "post",
        "/api/events/evt_01/assignments",
        "organizer",
        {"judge_id": "jdg_26", "project_ids": [other_track.pk]},
    )
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "other_track"


def test_declared_conflict_blocks_and_withdraws(seeded: None) -> None:
    project = unreviewed_project_for("jdg_26", {"trk_01", "trk_03"})
    call(
        "post",
        "/api/events/evt_01/assignments",
        "organizer",
        {"judge_id": "jdg_26", "project_ids": [project.pk]},
    )
    response = call(
        "post",
        "/api/events/evt_01/conflicts",
        "organizer",
        {"judge_id": "jdg_26", "team_id": project.team_id, "reason": "Former colleague"},
    )
    assert response.status_code == 201
    assert Assignment.objects.get(judge_id="jdg_26", project=project).status == "withdrawn"
    again = call(
        "post",
        "/api/events/evt_01/assignments",
        "organizer",
        {"judge_id": "jdg_26", "project_ids": [project.pk]},
    )
    assert again.status_code == 409


def test_progress_shows_every_judge_and_coverage(seeded: None) -> None:
    body = call("get", "/api/events/evt_01/progress", "organizer").json()
    assert len(body["judges"]) == 30
    assert {j["state"] for j in body["judges"]} <= {
        "not_started",
        "in_progress",
        "done",
        "overdue",
        "unassigned",
    }
    coverage = {c["project_id"]: c["reviews"] for c in body["coverage"]}
    assert coverage["prj_19"] == 2
    assert call("get", "/api/events/evt_01/progress", "judge_a").status_code == 403


def test_reviews_only_while_judging(seeded: None) -> None:
    project = unreviewed_project_for("jdg_26", {"trk_01", "trk_03"})
    assignment_id = call(
        "post",
        "/api/events/evt_01/assignments",
        "organizer",
        {"judge_id": "jdg_26", "project_ids": [project.pk]},
    ).json()[0]["id"]
    Event.objects.filter(pk="evt_01").update(phase="deliberation")
    response = call(
        "put",
        f"/api/judge/assignments/{assignment_id}/review",
        "judge_a",
        {"scores": {"quality": 4}},
    )
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "judging_closed"
