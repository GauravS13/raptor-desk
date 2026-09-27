import json
import logging
import re

import pytest
from django.urls import URLPattern, URLResolver, get_resolver

from core.logs import JsonFormatter, RedactSecrets, redact

SECRET = "Zx9SecretTokenValue_abc123"


def _format(record: logging.LogRecord) -> str:
    RedactSecrets().filter(record)
    return JsonFormatter().format(record)


def _routes(patterns: list, prefix: str = "") -> list[str]:
    found: list[str] = []
    for pattern in patterns:
        if isinstance(pattern, URLResolver):
            found += _routes(pattern.url_patterns, prefix + str(pattern.pattern))
        elif isinstance(pattern, URLPattern):
            found.append(prefix + str(pattern.pattern))
    return found


def test_json_formatter_writes_one_object_with_extras() -> None:
    record = logging.makeLogRecord(
        {"name": "raptor.test", "levelname": "INFO", "msg": "hello %s", "args": ("world",)}
    )
    record.route = "/events/<str:event_id>"
    entry = json.loads(_format(record))
    assert entry["msg"] == "hello world"
    assert entry["level"] == "info"
    assert entry["logger"] == "raptor.test"
    assert entry["route"] == "/events/<str:event_id>"
    assert entry["ts"].endswith("+00:00")


def test_every_route_with_a_token_is_redacted() -> None:
    token_routes = [r for r in _routes(get_resolver().url_patterns) if "token>" in r]
    assert token_routes, "expected invite and sign-in routes"
    for route in token_routes:
        path = "/" + re.sub(r"<[^>]*token>", SECRET, route)
        path = re.sub(r"<[^>]+>", "x", path)
        assert SECRET not in redact(f"Forbidden: {path}"), route


def test_django_error_lines_lose_the_token() -> None:
    record = logging.makeLogRecord(
        {"name": "django.request", "levelname": "ERROR", "msg": "Internal Server Error: %s"}
    )
    record.args = (f"/login/magic/{SECRET}",)
    assert SECRET not in _format(record)


@pytest.mark.django_db
def test_requests_are_logged_by_route_not_raw_path(client, caplog) -> None:
    with caplog.at_level(logging.INFO, logger="raptor.request"):
        response = client.get(f"/join/{SECRET}")
    lines = [r for r in caplog.records if r.name == "raptor.request"]
    assert len(lines) == 1
    assert lines[0].route == "/join/<str:token>"
    assert lines[0].status == response.status_code
    assert SECRET not in _format(lines[0])


@pytest.mark.django_db
def test_request_id_is_echoed_or_generated(client) -> None:
    assert client.get("/healthz", HTTP_X_REQUEST_ID="proxy-42").headers["X-Request-ID"] == (
        "proxy-42"
    )
    generated = client.get("/healthz", HTTP_X_REQUEST_ID="bad id\n").headers["X-Request-ID"]
    assert re.fullmatch(r"[0-9a-f]{32}", generated)


@pytest.mark.django_db
def test_health_probes_stay_out_of_the_info_log(client, caplog) -> None:
    with caplog.at_level(logging.INFO, logger="raptor.request"):
        client.get("/healthz")
    assert not [r for r in caplog.records if r.name == "raptor.request"]
