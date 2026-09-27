"""Prometheus metrics, read from the database so every web process reports the same numbers.

Only counts and ages are exposed: no names, emails, titles or scores.
"""

import shutil
from collections.abc import Iterable
from pathlib import Path

from django.conf import settings
from django.db.models import Count, Min

from apps.accounts.models import User
from apps.events.models import Event, Phase
from apps.judging.models import Assignment, Review
from apps.ops.backups import list_backups
from apps.submissions.models import Project
from core import clock
from core.models import AuditEvent, OutboxMessage
from core.version import VERSION

PREFIX = "raptor_desk_"


def _label(value: object) -> str:
    return str(value).replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")


class _Writer:
    def __init__(self) -> None:
        self.lines: list[str] = []

    def metric(
        self,
        name: str,
        kind: str,
        help_text: str,
        samples: Iterable[tuple[dict[str, object], float]],
    ) -> None:
        self.lines.append(f"# HELP {PREFIX}{name} {help_text}")
        self.lines.append(f"# TYPE {PREFIX}{name} {kind}")
        for labels, value in samples:
            rendered = ",".join(f'{k}="{_label(v)}"' for k, v in labels.items())
            braces = f"{{{rendered}}}" if rendered else ""
            number = int(value) if float(value).is_integer() else value
            self.lines.append(f"{PREFIX}{name}{braces} {number}")

    def text(self) -> str:
        return "\n".join(self.lines) + "\n"


def _per_event(queryset) -> list[tuple[dict[str, object], float]]:
    rows = queryset.values("event_id", "status").annotate(n=Count("pk")).order_by("event_id")
    return [({"event": r["event_id"], "status": r["status"]}, r["n"]) for r in rows]


def render() -> str:
    out = _Writer()
    out.metric(
        "info",
        "gauge",
        "Version and profile of this deployment.",
        [({"version": VERSION, "profile": settings.PROFILE}, 1)],
    )
    out.metric("users", "gauge", "Accounts.", [({}, User.objects.count())])

    by_phase = dict(Event.objects.values_list("phase").annotate(n=Count("pk")))
    out.metric(
        "events",
        "gauge",
        "Events by lifecycle phase.",
        [({"phase": phase}, by_phase.get(phase, 0)) for phase in Phase.values],
    )
    out.metric("projects", "gauge", "Projects by event and status.", _per_event(Project.objects))
    out.metric(
        "assignments",
        "gauge",
        "Judge assignments by event and status.",
        _per_event(Assignment.objects),
    )
    out.metric("reviews", "gauge", "Reviews by event and status.", _per_event(Review.objects))

    pending = OutboxMessage.objects.filter(done_at__isnull=True, failed_at__isnull=True)
    oldest = pending.aggregate(oldest=Min("next_attempt_at"))["oldest"]
    out.metric(
        "outbox_messages",
        "gauge",
        "Queued side effects (emails and others) by state.",
        [
            ({"state": "pending"}, pending.count()),
            ({"state": "failed"}, OutboxMessage.objects.filter(failed_at__isnull=False).count()),
            ({"state": "done"}, OutboxMessage.objects.filter(done_at__isnull=False).count()),
        ],
    )
    out.metric(
        "outbox_oldest_due_seconds",
        "gauge",
        "How long the oldest pending message has been due. Grows if the worker stops.",
        [({}, max((clock.now() - oldest).total_seconds(), 0) if oldest else 0)],
    )
    out.metric(
        "audit_events",
        "counter",
        "Entries in the append-only audit trail.",
        [({}, AuditEvent.objects.count())],
    )

    data = Path(settings.DATA_DIR)
    database = Path(str(settings.DATABASES["default"]["NAME"]))
    db_files = [p for p in (database, Path(f"{database}-wal")) if p.is_file()]
    out.metric(
        "database_bytes",
        "gauge",
        "Size of the SQLite database and its write-ahead log.",
        [({}, sum(p.stat().st_size for p in db_files))],
    )
    out.metric(
        "data_volume_free_bytes",
        "gauge",
        "Free space on the data volume.",
        [({}, shutil.disk_usage(data).free)],
    )
    backups = list_backups()
    out.metric(
        "last_backup_timestamp_seconds",
        "gauge",
        "When the newest backup was written (0 if none).",
        [({}, round(backups[-1].stat().st_mtime) if backups else 0)],
    )
    return out.text()
