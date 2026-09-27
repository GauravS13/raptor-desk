import re

import pytest

from apps.accounts.models import ApiToken, User

pytestmark = pytest.mark.django_db

SAMPLE = re.compile(r'^raptor_desk_[a-z_]+(\{[a-z_]+="[^"]*"(,[a-z_]+="[^"]*")*\})? -?[0-9.e+]+$')


def _bearer(email: str, *, admin: bool) -> dict[str, str]:
    user = User.objects.create_user(email, "pw-123456789", is_admin=admin)
    raw = f"rd_test_{user.pk}"
    ApiToken.objects.create(user=user, token_hash=ApiToken.hash(raw))
    return {"HTTP_AUTHORIZATION": f"Bearer {raw}"}


def test_admin_gets_prometheus_text(client, settings, tmp_path) -> None:
    settings.DATA_DIR = tmp_path
    response = client.get("/metrics", **_bearer("admin@example.org", admin=True))
    assert response.status_code == 200
    assert response["Content-Type"].startswith("text/plain; version=0.0.4")
    body = response.content.decode()
    for line in body.splitlines():
        assert line.startswith("# ") or SAMPLE.match(line), line
    assert 'raptor_desk_events{phase="judging"} 0' in body
    assert "raptor_desk_users 1" in body


def test_metrics_never_leak_personal_data(client, settings, tmp_path) -> None:
    settings.DATA_DIR = tmp_path
    body = client.get("/metrics", **_bearer("admin@example.org", admin=True)).content.decode()
    assert "@" not in body


def test_others_get_json_errors_not_a_login_redirect(client) -> None:
    anonymous = client.get("/metrics")
    assert anonymous.status_code == 401
    assert anonymous.json()["error"]["code"] == "unauthorized"
    organizer = client.get("/metrics", **_bearer("org@example.org", admin=False))
    assert organizer.status_code == 403
