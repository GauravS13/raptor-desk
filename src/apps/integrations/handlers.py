"""Outbox handlers owned by the integrations app (discovered by run_outbox)."""

from typing import Any

from apps.integrations import webhooks
from core.outbox import handler


@handler("webhook")
def deliver_webhook(payload: dict[str, Any]) -> None:
    webhooks.deliver(payload)
