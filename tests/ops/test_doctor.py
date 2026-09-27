import io
import json
from datetime import timedelta
from pathlib import Path

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import connection

from apps.accounts.models import ApiToken, User
from apps.ops import doctor
from core import clock
from core.models import OutboxMessage

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def data_dir(settings, tmp_path: Path) -> Path:
    settings.DATA_DIR = tmp_path
    settings.EMAIL_BACKEND = "django.core.mail.backends.smtp.EmailBackend"
    settings.EMAIL_HOST, settings.EMAIL_PORT = "127.0.0.1", 9  # nothing listens here
    return tmp_path


def _by_name() -> dict[str, doctor.Finding]:
    return {f.name: f for f in doctor.run()}


def test_a_fresh_portal_has_no_failures() -> None:
    findings = _by_name()
    assert not [f for f in findings.values() if f.status == doctor.FAIL]
    for name in ("database", "migrations", "access policies", "tamper guards", "data volume"):
        assert findings[name].status == doctor.OK, findings[name]
    assert findings["email"].status == doctor.WARN


def test_backups_warn_until_one_is_recent(data_dir: Path) -> None:
    assert _by_name()["backups"].status == doctor.WARN
    (data_dir / "backups").mkdir()
    (data_dir / "backups" / "raptor-desk-20260101-000000.tar.gz").write_bytes(b"x")
    assert _by_name()["backups"].status == doctor.OK


def test_overdue_outbox_points_at_the_worker() -> None:
    OutboxMessage.objects.create(kind="email", next_attempt_at=clock.now() - timedelta(minutes=30))
    finding = _by_name()["outbox"]
    assert finding.status == doctor.WARN
    assert "worker" in finding.detail


def test_missing_tamper_guard_fails() -> None:
    with connection.cursor() as cursor:
        cursor.execute("DROP TRIGGER core_auditevent_no_delete")
    finding = _by_name()["tamper guards"]
    assert finding.status == doctor.FAIL
    assert "audit trail" in finding.detail


def test_production_with_demo_tokens_fails_and_the_command_exits_1(settings) -> None:
    settings.PROFILE = "production"
    user = User.objects.create_user("x@example.org", "pw-123456789")
    ApiToken.objects.create(user=user, token_hash=ApiToken.hash("rd_seed_x"), is_seed=True)
    assert _by_name()["profile"].status == doctor.FAIL
    out = io.StringIO()
    with pytest.raises(CommandError, match="failed"):
        call_command("doctor", stdout=out)
    assert "fix:" in out.getvalue()


def test_json_output_lists_every_check() -> None:
    out = io.StringIO()
    call_command("doctor", "--json", stdout=out)
    names = [f["name"] for f in json.loads(out.getvalue())]
    assert len(names) == len(doctor.CHECKS)
