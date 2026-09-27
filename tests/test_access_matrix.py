"""Isolation, proven: expected vs actual HTTP status for every role on every sensitive resource.

Run with RD_WRITE_MATRIX=1 to regenerate ACCESS-MATRIX.md from the actual responses.
"""

import os
import re
from io import StringIO
from pathlib import Path

import pytest
from django.conf import settings
from django.core.management import call_command
from django.test import Client
from django.urls import get_resolver

from apps.accounts.models import ApiToken, User
from apps.judging.models import Assignment
from core.checks import iter_url_patterns
from core.policy import iter_api_operations

ROOT = Path(__file__).resolve().parent.parent

IDENTITIES = [
    ("anonymous", None, "no credentials"),
    ("participant", "priya1@example.org", "member of team tm_01"),
    ("participant_b", "lena2@example.org", "member of team tm_02"),
    ("judge_a", "jonas.vogel@example.org", "jdg_26: Developer tools, Accessibility"),
    (
        "judge_b",
        "diego.herrera@example.org",
        "jdg_24: Developer tools, Education (peer of judge_a)",
    ),
    ("judge_c", "wei.lindqvist@example.org", "jdg_02: Data and analytics, Security (other tracks)"),
    ("organizer", "organizer@raptor-desk.local", "organizer of evt_01"),
    ("admin", "admin@raptor-desk.local", "platform admin"),
]

# column: (label, path template, expected status per identity)
COLUMNS = [
    (
        "Own scores",
        "/api/judge/scores",
        {
            "anonymous": 401,
            "participant": 403,
            "participant_b": 403,
            "judge_a": 200,
            "judge_b": 200,
            "judge_c": 200,
            "organizer": 403,
            "admin": 403,
        },
    ),
    (
        "Peer scores (jdg_26's)",
        "/api/judges/jdg_26/scores?event=evt_01",
        {
            "anonymous": 401,
            "participant": 403,
            "participant_b": 403,
            "judge_a": 200,
            "judge_b": 403,
            "judge_c": 403,
            "organizer": 200,
            "admin": 200,
        },
    ),
    (
        "Another judge's assignment",
        "/api/judge/assignments/{assignment}",
        {
            "anonymous": 401,
            "participant": 403,
            "participant_b": 403,
            "judge_a": 200,
            "judge_b": 404,
            "judge_c": 404,
            "organizer": 403,
            "admin": 403,
        },
    ),
    (
        "Aggregate results",
        "/api/events/evt_01/results",
        {
            "anonymous": 401,
            "participant": 403,
            "participant_b": 403,
            "judge_a": 403,
            "judge_b": 403,
            "judge_c": 403,
            "organizer": 200,
            "admin": 200,
        },
    ),
    (
        "Audit log",
        "/api/events/evt_01/audit",
        {
            "anonymous": 401,
            "participant": 403,
            "participant_b": 403,
            "judge_a": 403,
            "judge_b": 403,
            "judge_c": 403,
            "organizer": 200,
            "admin": 200,
        },
    ),
    (
        "CSV export",
        "/api/events/evt_01/exports/reviews.csv",
        {
            "anonymous": 401,
            "participant": 403,
            "participant_b": 403,
            "judge_a": 403,
            "judge_b": 403,
            "judge_c": 403,
            "organizer": 200,
            "admin": 200,
        },
    ),
]


@pytest.fixture
def world(db) -> dict:
    call_command(
        "seed", fixtures=str(Path(settings.REPO_DIR) / "data" / "fixtures.json"), stdout=StringIO()
    )
    tokens = {}
    for name, email, _ in IDENTITIES:
        if email:
            tokens[name] = ApiToken.issue(User.objects.get(email=email), "matrix")[1]
    target = Assignment.objects.filter(judge_id="jdg_26").order_by("pk").first()
    return {"tokens": tokens, "assignment": target.pk}


def request(world: dict, identity: str, path: str) -> int:
    headers = {}
    token = world["tokens"].get(identity)
    if token:
        headers["HTTP_AUTHORIZATION"] = f"Bearer {token}"
    return Client().get(path.format(assignment=world["assignment"]), **headers).status_code


def test_isolation_matrix(world: dict) -> None:
    actual = {
        label: {name: request(world, name, path) for name, _, _ in IDENTITIES}
        for label, path, _ in COLUMNS
    }
    mismatches = [
        f"{label} as {name}: expected {expected[name]}, got {actual[label][name]}"
        for label, _, expected in COLUMNS
        for name in expected
        if actual[label][name] != expected[name]
    ]
    if os.environ.get("RD_WRITE_MATRIX"):
        _write_markdown(actual)
    assert mismatches == []


SUBSTITUTIONS = {
    "event_id": "evt_01",
    "project_id": "prj_01",
    "team_id": "tm_01",
    "judge_id": "jdg_26",
    "user_id": "jdg_26",
    "role": "judge",
    "kind": "results",
    "token": "not-a-real-token",
}


def _concrete(route: str, world: dict) -> str | None:
    path = "/" + route.lstrip("^").rstrip("$")
    path = re.sub(r"<(?:\w+:)?assignment_id>", world["assignment"], path)
    for key, value in SUBSTITUTIONS.items():
        path = re.sub(rf"<(?:\w+:)?{key}>", value, path)
        path = path.replace("{" + key + "}", value)
    path = path.replace("{assignment_id}", world["assignment"])
    if "<" in path or "{" in path or path.startswith("/admin"):
        return None
    return path


def test_no_route_fails_or_redirects_the_api_for_any_role(world: dict) -> None:
    routes = [route for route, _ in iter_url_patterns(get_resolver().url_patterns)]
    routes += [
        f"api/{path.lstrip('/')}" for methods, path, _ in iter_api_operations() if "GET" in methods
    ]
    problems = []
    for route in routes:
        path = _concrete(route, world)
        if path is None:
            continue
        for name, _, _ in IDENTITIES:
            status = request(world, name, path)
            if status >= 500:
                problems.append(f"{path} as {name}: {status}")
            if path.startswith("/api/") and 300 <= status < 400:
                problems.append(f"{path} as {name}: API redirected ({status})")
    assert problems == []


def _write_markdown(actual: dict[str, dict[str, int]]) -> None:
    labels = [label for label, _, _ in COLUMNS]
    lines = [
        "# Access matrix",
        "",
        "Generated by `tests/test_access_matrix.py` from real HTTP responses against the",
        "seeded fixtures (`RD_WRITE_MATRIX=1 uv run pytest tests/test_access_matrix.py`).",
        "Every cell is the status the API returned; the test fails if any cell differs from",
        "the expected value. 401 = not signed in, 403 = refused, 404 = presented as not existing.",
        "",
        "| Identity | " + " | ".join(labels) + " |",
        "|---|" + "---|" * len(labels),
    ]
    for name, _, note in IDENTITIES:
        cells = []
        for label in labels:
            code = actual[label][name]
            cells.append(f"**{code}** ✓" if code == 200 else f"{code}")
        lines.append(f"| {name} <br><small>{note}</small> | " + " | ".join(cells) + " |")
    lines += [
        "",
        "Requests used:",
        "",
        *[f"- **{label}**: `GET {path}`" for label, path, _ in COLUMNS],
        "",
        "The same suite also requests every GET route of the portal and the API as each identity",
        "and fails on any server error or any API redirect.",
    ]
    (ROOT / "ACCESS-MATRIX.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
