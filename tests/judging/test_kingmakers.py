from io import StringIO
from pathlib import Path

import pytest
from django.conf import settings
from django.core.management import call_command
from django.test import Client

from apps.seed.demo import SEED_LOGINS

TOKENS = {login.role: login.token for login in SEED_LOGINS}


@pytest.fixture
def seeded(db) -> None:
    fixtures = Path(settings.REPO_DIR) / "data" / "fixtures.json"
    call_command("seed", fixtures=str(fixtures), stdout=StringIO())


def get(path: str, role: str | None):
    headers = {"HTTP_AUTHORIZATION": f"Bearer {TOKENS[role]}"} if role else {}
    return Client().get(path, **headers)


def test_api_names_the_judges_who_alone_decide_a_prize(seeded: None) -> None:
    response = get("/api/events/evt_01/kingmakers", "organizer")
    assert response.status_code == 200
    body = response.json()
    assert body["prize_places"] >= 1
    found = {k["judge_id"]: k for k in body["kingmakers"]}
    assert found, "the fixtures have fragile prize places"
    for item in found.values():
        assert item["entered"] or item["left"] or item["winner_before"] != item["winner_after"]
        assert set(item["entered"]).isdisjoint(item["left"])


def test_kingmakers_are_for_organizers_only(seeded: None) -> None:
    assert get("/api/events/evt_01/kingmakers", "judge_a").status_code == 403
    assert get("/api/events/evt_01/kingmakers", "participant").status_code == 403
    assert get("/api/events/evt_01/kingmakers", None).status_code == 401


def test_results_page_shows_the_kingmaker_check(seeded: None) -> None:
    page = get("/o/events/evt_01/results", "organizer").content.decode()
    assert "Kingmaker check" in page
    assert "jdg_24" in page
