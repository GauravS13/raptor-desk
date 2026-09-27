"""A health report an organizer can read: what is fine, what needs attention, and what to do.

Each check returns a :class:`Finding`. ``fail`` means the portal is broken or
unsafe as configured; ``warn`` means it works but needs attention soon.
"""

import os
import shutil
import socket
import tempfile
from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import timedelta
from pathlib import Path

from django.conf import settings
from django.core import checks as django_checks
from django.db import connection
from django.db.migrations.executor import MigrationExecutor

from apps.accounts.models import ApiToken
from apps.ops.backups import list_backups
from core import clock, signing
from core.models import OutboxMessage

OK, INFO, WARN, FAIL = "ok", "info", "warn", "fail"

# Database triggers that make history tamper-proof (see core/dbguards.py).
GUARD_TRIGGERS = {
    "core_auditevent_no_update": "audit trail",
    "core_auditevent_no_delete": "audit trail",
    "events_phasetransition_no_update": "phase history",
    "events_phasetransition_no_delete": "phase history",
    "submissions_projectversion_frozen_after_submitted_at": "submitted versions",
}
OUTBOX_OVERDUE = timedelta(minutes=5)
BACKUP_MAX_AGE = timedelta(hours=24)
LOW_DISK = 1 << 30
CRITICAL_DISK = 100 << 20


@dataclass(frozen=True)
class Finding:
    name: str
    status: str
    detail: str
    fix: str = ""

    def as_dict(self) -> dict[str, str]:
        return asdict(self)


def _size(n: float) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} GB"


def database() -> Finding:
    try:
        with connection.cursor() as cursor:
            if connection.vendor != "sqlite":
                cursor.execute("SELECT 1")
                return Finding("database", OK, f"{connection.vendor} reachable")
            cursor.execute("PRAGMA quick_check")
            integrity = cursor.fetchone()[0]
            cursor.execute("PRAGMA journal_mode")
            mode = cursor.fetchone()[0]
    except Exception as exc:
        return Finding("database", FAIL, f"cannot query the database: {exc}")
    if integrity != "ok":
        return Finding(
            "database",
            FAIL,
            f"integrity check failed: {integrity}",
            "restore the latest backup: make restore FILE=<name>",
        )
    path = Path(str(settings.DATABASES["default"]["NAME"]))
    files = [p for p in (path, Path(f"{path}-wal")) if p.is_file()]
    size = f", {_size(sum(p.stat().st_size for p in files))}" if files else ""
    if mode == "memory":
        return Finding("database", OK, "SQLite in memory (test run), integrity ok")
    if mode != "wal":
        return Finding(
            "database",
            WARN,
            f"SQLite in {mode} mode{size}",
            "WAL mode is set on every connection; check the data volume supports it",
        )
    return Finding("database", OK, f"SQLite, WAL, integrity ok{size}")


def migrations() -> Finding:
    executor = MigrationExecutor(connection)
    plan = executor.migration_plan(executor.loader.graph.leaf_nodes())
    if plan:
        return Finding(
            "migrations",
            FAIL,
            f"{len(plan)} migration(s) not applied",
            "python src/manage.py migrate (the container does this on every start)",
        )
    applied = len(executor.loader.applied_migrations)
    return Finding("migrations", OK, f"all {applied} applied")


def access_policies() -> Finding:
    errors = [m for m in django_checks.run_checks() if m.level >= django_checks.ERROR]
    if errors:
        ids = ", ".join(sorted({m.id or "?" for m in errors}))
        return Finding(
            "access policies",
            FAIL,
            f"{len(errors)} configuration error(s): {ids}",
            "python src/manage.py check",
        )
    return Finding("access policies", OK, "every route declares who may use it")


def tamper_guards() -> Finding:
    if connection.vendor != "sqlite":
        return Finding("tamper guards", INFO, "checked on SQLite only")
    with connection.cursor() as cursor:
        cursor.execute("SELECT name FROM sqlite_master WHERE type = 'trigger'")
        present = {row[0] for row in cursor.fetchall()}
    missing = sorted({GUARD_TRIGGERS[n] for n in GUARD_TRIGGERS if n not in present})
    if missing:
        return Finding(
            "tamper guards",
            FAIL,
            f"history can be rewritten: {', '.join(missing)} unprotected",
            "python src/manage.py migrate",
        )
    return Finding("tamper guards", OK, "audit trail, phase history and submissions append-only")


def data_volume() -> Finding:
    data = Path(settings.DATA_DIR)
    try:
        with tempfile.NamedTemporaryFile(dir=data):
            pass
    except OSError as exc:
        return Finding("data volume", FAIL, f"{data} is not writable: {exc}")
    free = shutil.disk_usage(data).free
    if free < CRITICAL_DISK:
        return Finding("data volume", FAIL, f"only {_size(free)} free", "free disk space now")
    if free < LOW_DISK:
        return Finding("data volume", WARN, f"{_size(free)} free", "free disk space soon")
    return Finding("data volume", OK, f"writable, {_size(free)} free")


def keys() -> Finding:
    source = "RD_SECRET_KEY" if os.environ.get("RD_SECRET_KEY") else "data volume"
    seed = Path(settings.DATA_DIR) / "keys" / "ed25519.seed"
    if not seed.is_file():
        return Finding("keys", OK, f"secret key from {source}; signing key is created on first use")
    try:
        key_id = signing.key_id()
    except Exception as exc:
        return Finding("keys", FAIL, f"signing key unreadable: {exc}", "restore keys/ from backup")
    if os.name == "posix" and seed.stat().st_mode & 0o077:
        return Finding(
            "keys", WARN, f"signing key {key_id} is readable by other users", f"chmod 600 {seed}"
        )
    return Finding("keys", OK, f"secret key from {source}; signing key {key_id}")


def outbox() -> Finding:
    pending = OutboxMessage.objects.filter(done_at__isnull=True, failed_at__isnull=True)
    overdue = pending.filter(next_attempt_at__lt=clock.now() - OUTBOX_OVERDUE).count()
    failed = OutboxMessage.objects.filter(failed_at__isnull=False)
    if overdue:
        return Finding(
            "outbox",
            WARN,
            f"{overdue} message(s) overdue by more than 5 minutes: is the worker running?",
            "docker compose ps worker; docker compose logs worker",
        )
    if failed.exists():
        latest = failed.order_by("-failed_at").first()
        reason = (latest.last_error or "unknown error").splitlines()[0][:120] if latest else ""
        return Finding(
            "outbox",
            WARN,
            f"{failed.count()} message(s) gave up after retries; latest: {reason}",
            "check the email settings (RD_EMAIL_*)",
        )
    return Finding("outbox", OK, f"{pending.count()} pending, none overdue, none failed")


def email() -> Finding:
    if not settings.EMAIL_BACKEND.endswith("smtp.EmailBackend"):
        return Finding("email", INFO, f"backend {settings.EMAIL_BACKEND}")
    target = f"{settings.EMAIL_HOST}:{settings.EMAIL_PORT}"
    try:
        with socket.create_connection((settings.EMAIL_HOST, settings.EMAIL_PORT), timeout=2):
            pass
    except OSError:
        return Finding(
            "email",
            WARN,
            f"cannot reach the mail server at {target}; emails wait in the outbox",
            "check RD_EMAIL_HOST and RD_EMAIL_PORT",
        )
    return Finding("email", OK, f"mail server reachable at {target}")


def profile() -> Finding:
    if settings.PROFILE == "demo":
        return Finding(
            "profile",
            WARN if settings.DEBUG else INFO,
            "demo: fixed test tokens and demo passwords are active"
            + ("; DEBUG is on" if settings.DEBUG else ""),
            "for a real event set RD_PROFILE=production and never expose a demo portal",
        )
    if settings.DEBUG:
        return Finding("profile", FAIL, "production with DEBUG on", "unset RD_DEBUG")
    seeded = ApiToken.objects.filter(is_seed=True, revoked_at__isnull=True).count()
    if seeded:
        return Finding(
            "profile",
            FAIL,
            f"production with {seeded} active demo token(s)",
            "start from an empty data volume, or revoke the seed tokens",
        )
    problems = []
    if not settings.SESSION_COOKIE_SECURE:
        problems.append("cookies not marked secure (set RD_SECURE_COOKIES=1 behind HTTPS)")
    if "localhost" in settings.BASE_URL:
        problems.append("RD_BASE_URL is localhost, so emailed links will not work")
    if problems:
        return Finding("profile", WARN, "production: " + "; ".join(problems))
    return Finding("profile", OK, "production: no demo tokens, DEBUG off, secure cookies")


def backups() -> Finding:
    found = list_backups()
    if not found:
        return Finding(
            "backups", WARN, "no backup yet", "make backup (or: python src/manage.py backup)"
        )
    latest = found[-1]
    age = clock.now().timestamp() - latest.stat().st_mtime
    hours = age / 3600
    if age > BACKUP_MAX_AGE.total_seconds():
        return Finding(
            "backups", WARN, f"latest is {hours:.0f} hours old ({latest.name})", "make backup"
        )
    return Finding("backups", OK, f"latest {latest.name}, {hours:.1f} hours ago")


CHECKS: list[Callable[[], Finding]] = [
    database,
    migrations,
    access_policies,
    tamper_guards,
    data_volume,
    keys,
    outbox,
    email,
    profile,
    backups,
]


def run() -> list[Finding]:
    findings = []
    for check in CHECKS:
        try:
            findings.append(check())
        except Exception as exc:
            name = check.__name__.replace("_", " ")
            findings.append(Finding(name, FAIL, f"check crashed: {exc}"))
    return findings
