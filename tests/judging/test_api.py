import csv
import io
from io import StringIO
from pathlib import Path

import pytest
from django.conf import settings
from django.core.management import call_command
from django.test import Client

from apps.accounts.models import ApiToken, RoleGrant, User
from apps.judging.exports import EXPORTS, safe
from apps.judging.models import Review
from apps.seed.demo import SEED_LOGINS

TOKENS = {login.role: login.token for login in SEED_LOGINS}


@pytest.fixture
def seeded(db) -> None:
    fixtures = Path(settings.REPO_DIR) / "data" / "fixtures.json"
    call_command("seed", fixtures=str(fixtures), stdout=StringIO())


def as_role(role: str | None) -> dict[str, str]:
    return {"HTTP_AUTHORIZATION": f"Bearer {TOKENS[role]}"} if role else {}


def get(path: str, role: str | None):
    return Client().get(path, **as_role(role))


def test_judge_reads_only_their_own_scores(seeded: None) -> None:
    response = get("/api/judge/scores", "judge_a")
    assert response.status_code == 200
    reviews = response.json()
    assert reviews and {r["judge_id"] for r in reviews} == {"jdg_26"}
    assert len(reviews) == Review.objects.filter(judge_id="jdg_26").count()
    assert {s["criterion"] for s in reviews[0]["scores"]} == {
        "functionality",
        "quality",
        "innovation",
    }


def test_peer_judge_is_refused_with_403_even_when_they_share_projects(seeded: None) -> None:
    shared = set(Review.objects.filter(judge_id="jdg_26").values_list("project_id", flat=True))
    assert shared & set(
        Review.objects.filter(judge_id="jdg_24").values_list("project_id", flat=True)
    )
    for path in ("/api/judges/jdg_26/scores", "/api/judges/jdg_26/scores?event=evt_01"):
        response = get(path, "judge_b")
        assert response.status_code == 403
        assert "Location" not in response.headers
        assert "jdg_26" not in response.content.decode()


def test_participant_and_anonymous_are_refused(seeded: None) -> None:
    assert get("/api/judge/scores", "participant").status_code == 403
    assert get("/api/judge/scores", None).status_code == 401
    assert get("/api/judges/jdg_26/scores", "participant").status_code == 403


def test_organizer_reads_a_judges_scores_in_their_event(seeded: None) -> None:
    response = get("/api/judges/jdg_26/scores?event=evt_01", "organizer")
    assert response.status_code == 200
    assert response.json()


def test_organizer_of_another_event_is_refused(seeded: None) -> None:
    outsider = User.objects.create_user("other-org@example.org")
    RoleGrant.objects.create(user=outsider, event_id="evt_dogfood_2026", role="organizer")
    _, raw = ApiToken.issue(outsider, "t")
    response = Client().get(
        "/api/judges/jdg_26/scores?event=evt_01", HTTP_AUTHORIZATION=f"Bearer {raw}"
    )
    assert response.status_code == 403


def test_results_csv_for_the_organizer(seeded: None) -> None:
    response = get("/api/events/evt_01/exports/results.csv", "organizer")
    assert response.status_code == 200
    assert response["Content-Type"].startswith("text/csv")
    text = response.content.decode()
    assert "," in text.splitlines()[0]
    rows = list(csv.DictReader(io.StringIO(text)))
    assert len(rows) == 40
    assert rows[0]["raw_rank"] == "1"


def test_every_export_is_valid_csv(seeded: None) -> None:
    for kind in EXPORTS:
        response = get(f"/api/events/evt_01/exports/{kind}.csv", "organizer")
        assert response.status_code == 200, kind
        header = next(csv.reader(io.StringIO(response.content.decode())))
        assert len(header) > 1, kind


def test_exports_are_organizer_only(seeded: None) -> None:
    assert get("/api/events/evt_01/exports/results.csv", "judge_a").status_code == 403
    assert get("/api/events/evt_01/exports/results.csv", "participant").status_code == 403
    assert get("/api/events/evt_01/exports/nope.csv", "organizer").status_code == 404


def test_csv_cells_cannot_run_formulas() -> None:
    assert safe('=HYPERLINK("http://evil")') == '\'=HYPERLINK("http://evil")'
    assert safe("+1") == "'+1"
    assert safe("Solid.") == "Solid."
