"""Transactional outbox: enqueue side effects inside a transaction, deliver them later.

Handlers are registered per message kind (``@handler("email")``) in each app's
``handlers`` module. The worker (``manage.py run_outbox``) claims due messages,
runs their handler, and retries failures with exponential backoff.
"""

import logging
from collections.abc import Callable
from datetime import timedelta
from typing import Any

from django.db.models import Q

from core import clock
from core.models import OutboxMessage

log = logging.getLogger(__name__)

MAX_ATTEMPTS = 8
CLAIM_SECONDS = 120

Handler = Callable[[dict[str, Any]], None]
_HANDLERS: dict[str, Handler] = {}


def handler(kind: str) -> Callable[[Handler], Handler]:
    def register(fn: Handler) -> Handler:
        _HANDLERS[kind] = fn
        return fn

    return register


def enqueue(
    kind: str,
    payload: dict[str, Any],
    *,
    dedupe_key: str | None = None,
    delay_seconds: int = 0,
) -> OutboxMessage:
    """Queue a message. With ``dedupe_key`` the same logical message is only queued once."""
    if dedupe_key:
        existing = OutboxMessage.objects.filter(dedupe_key=dedupe_key).first()
        if existing is not None:
            return existing
    return OutboxMessage.objects.create(
        kind=kind,
        payload=payload,
        dedupe_key=dedupe_key,
        next_attempt_at=clock.now() + timedelta(seconds=delay_seconds),
    )


def _unclaimed(now: Any) -> Q:
    return Q(claimed_until__isnull=True) | Q(claimed_until__lt=now)


def backoff_seconds(attempts: int) -> int:
    return int(min(3600, 2**attempts))


def process_due(limit: int = 50) -> int:
    """Deliver up to ``limit`` due messages. Returns how many succeeded."""
    now = clock.now()
    due_ids = list(
        OutboxMessage.objects.filter(
            done_at__isnull=True, failed_at__isnull=True, next_attempt_at__lte=now
        )
        .filter(_unclaimed(now))
        .order_by("created_at")
        .values_list("id", flat=True)[:limit]
    )
    delivered = 0
    for message_id in due_ids:
        claimed = (
            OutboxMessage.objects.filter(pk=message_id, done_at__isnull=True)
            .filter(_unclaimed(now))
            .update(claimed_until=now + timedelta(seconds=CLAIM_SECONDS))
        )
        if not claimed:
            continue  # another worker took it
        message = OutboxMessage.objects.get(pk=message_id)
        run = _HANDLERS.get(message.kind)
        attempts = message.attempts + 1
        try:
            if run is None:
                raise LookupError(f"no handler registered for {message.kind!r}")
            run(message.payload)
        except Exception as exc:
            fields: dict[str, Any] = {
                "attempts": attempts,
                "claimed_until": None,
                "last_error": f"{type(exc).__name__}: {exc}"[:2000],
            }
            if attempts >= MAX_ATTEMPTS:
                fields["failed_at"] = clock.now()
            else:
                fields["next_attempt_at"] = clock.now() + timedelta(
                    seconds=backoff_seconds(attempts)
                )
            OutboxMessage.objects.filter(pk=message_id).update(**fields)
            log.warning(
                "outbox %s %s failed (attempt %s): %s", message.kind, message_id, attempts, exc
            )
        else:
            OutboxMessage.objects.filter(pk=message_id).update(
                done_at=clock.now(), claimed_until=None, attempts=attempts, last_error=""
            )
            delivered += 1
    return delivered
