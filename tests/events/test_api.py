from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from django.test import Client

from apps.accounts.models import ApiToken, RoleGrant, User
from apps.events.models import Event

CLOSE = (datetime.now(UTC) + timedelta(days=3)).isoformat()


class Api:
    def __init__(self, raw: str | None = None) -> None:
        self.client = Client()
        self.headers = {"HTTP_AUTHORIZATION": f"Bearer {raw}"} if raw else {}

    def get(self, path: str) -> Any:
        return self.client.get(path, **self.headers)

    def post(self, path: str, body: dict[str, Any] | None = None) -> Any:
        return self.client.post(path, body or {}, content_type="application/json", **self.headers)

    def patch(self, path: str, body: dict[str, Any]) -> Any:
        return self.client.patch(path, body, content_type="application/json", **self.headers)

    def put(self, path: str, body: dict[str, Any]) -> Any:
        return self.client.put(path, body, content_type="application/json", **self.headers)

    def delete(self, path: str) -> Any:
        return self.client.delete(path, **self.headers)


def token_for(email: str, *, admin: bool = False) -> str:
    user = User.objects.create_user(email, is_admin=admin)
    return ApiToken.issue(user, "test")[1]


@pytest.fixture
def admin_api(db) -> Api:
    return Api(token_for("admin@example.org", admin=True))


@pytest.fixture
def event(admin_api: Api) -> dict[str, Any]:
    response = admin_api.post(
        "/api/events",
        {"name": "Spring Hack", "template": "dogfood-2026", "submissions_close_at": CLOSE},
    )
    assert response.status_code == 201, response.content
    return response.json()


def test_admin_creates_event_from_template(event: dict[str, Any], admin_api: Api) -> None:
    assert event["phase"] == "draft"
    assert event["template_key"] == "dogfood-2026"
    rubric = admin_api.get(f"/api/events/{event['id']}/rubric").json()
    assert rubric["weighting"] == "percent"
    assert len(rubric["criteria"]) == 9


def test_anonymous_cannot_create_events(db) -> None:
    response = Api().post("/api/events", {"name": "Nope"})
    assert response.status_code == 401


def test_plain_user_cannot_create_events(db) -> None:
    response = Api(token_for("someone@example.org")).post("/api/events", {"name": "Nope"})
    assert response.status_code == 403


def test_drafts_are_invisible_to_outsiders(event: dict[str, Any]) -> None:
    assert Api().get(f"/api/events/{event['id']}").status_code == 404
    assert event["id"] not in [e["id"] for e in Api().get("/api/events").json()]
    Event.objects.filter(pk=event["id"]).update(phase="submissions")
    assert Api().get(f"/api/events/{event['id']}").status_code == 200


def test_only_this_events_organizers_can_manage_it(event: dict[str, Any]) -> None:
    other_org = token_for("other-org@example.org")
    other_event = Event.objects.create(slug="other", name="Other")
    RoleGrant.objects.create(
        user=User.objects.get(email="other-org@example.org"), event=other_event, role="organizer"
    )
    judge_raw = token_for("judge@example.org")
    RoleGrant.objects.create(
        user=User.objects.get(email="judge@example.org"), event_id=event["id"], role="judge"
    )
    path = f"/api/events/{event['id']}"
    assert Api(other_org).patch(path, {"name": "Hijacked"}).status_code == 403
    assert Api(judge_raw).patch(path, {"name": "Hijacked"}).status_code == 403
    assert Api().patch(path, {"name": "Hijacked"}).status_code == 401


def test_organizer_manages_rubric_tracks_people_and_phase(
    event: dict[str, Any], admin_api: Api
) -> None:
    base = f"/api/events/{event['id']}"
    rubric = {
        "weighting": "percent",
        "criteria": [
            {"key": "impact", "label": "Impact", "weight": 60},
            {"key": "craft", "label": "Craft", "weight": 40},
        ],
    }
    assert admin_api.put(f"{base}/rubric", rubric).status_code == 200
    bad = {"weighting": "percent", "criteria": [{"key": "x", "label": "X", "weight": 50}]}
    response = admin_api.put(f"{base}/rubric", bad)
    assert response.status_code == 422
    assert "sum to 100" in str(response.json()["error"]["details"])

    assert admin_api.post(f"{base}/tracks", {"name": "Security"}).status_code == 201
    assert admin_api.post(f"{base}/tracks", {"name": "Security"}).status_code == 409

    granted = admin_api.post(f"{base}/people", {"email": "Judge@Example.org", "role": "judge"})
    assert granted.status_code == 201
    user_id = granted.json()["user_id"]
    people = {(p["email"], p["role"]) for p in admin_api.get(f"{base}/people").json()}
    assert ("judge@example.org", "judge") in people
    assert admin_api.delete(f"{base}/people/{user_id}/judge").status_code == 204

    checks = admin_api.get(f"{base}/preflight?to=submissions").json()
    assert checks == []
    moved = admin_api.post(f"{base}/phase", {"to": "submissions"})
    assert moved.status_code == 200
    assert moved.json()["to_phase"] == "submissions"


def test_blocked_phase_change_returns_the_checks(event: dict[str, Any], admin_api: Api) -> None:
    base = f"/api/events/{event['id']}"
    Event.objects.filter(pk=event["id"]).update(phase="eligibility")
    response = admin_api.post(f"{base}/phase", {"to": "judging"})
    assert response.status_code == 409
    body = response.json()["error"]
    assert body["code"] == "preflight_blocked"
    assert {c["code"] for c in body["details"]["checks"]} >= {"no_judges"}


def test_templates_are_listed_for_signed_in_users(db) -> None:
    assert Api().get("/api/event-templates").status_code == 401
    keys = {t["key"] for t in Api(token_for("u@example.org")).get("/api/event-templates").json()}
    assert "dogfood-2026" in keys
