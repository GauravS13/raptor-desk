"""Webhooks: signed JSON deliveries when something happens in an event.

Each delivery is a POST of a JSON body:

    {"id": "<delivery id>", "event": "comment.created", "event_id": "evt_01",
     "created_at": "...", "data": {...}}

with the header ``X-Dogfood-Signature: sha256=<hex>``, the HMAC-SHA256 of
the exact body bytes under the webhook's secret. A receiver recomputes the
HMAC and compares in constant time. Deliveries go through the outbox, so a
change and its webhook are committed together, and failed deliveries are
retried with backoff.

Payloads never carry scores, tallies or email addresses.
"""

import hashlib
import hmac
import json
import secrets
import urllib.error
import urllib.request
from typing import Any

from django.db import transaction
from django.db.models import Q

from apps.accounts.models import RoleGrant
from apps.events import hooks
from apps.events.models import Event, Phase
from apps.integrations.models import Webhook
from core import audit, clock, outbox
from core.http import forbidden, not_found, unprocessable
from core.ids import new_id
from core.policy import Principal

EVENTS = (
    "comment.created",
    "project.submitted",
    "event.phase_changed",
    "results.published",
    "ping",
)
SIGNATURE_HEADER = "X-Dogfood-Signature"
TIMEOUT_SECONDS = 5


def sign(secret: str, body: bytes) -> str:
    return "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def _may_manage(principal: Principal, event_id: str | None) -> bool:
    if principal.is_admin:
        return True
    if event_id is None:
        return bool(principal.events_with_role("organizer"))
    return principal.has_role("organizer", event_id)


@transaction.atomic
def create(
    principal: Principal, url: str, events: list[str], event_id: str | None = None
) -> tuple[Webhook, str]:
    if not url.startswith(("https://", "http://")):
        raise unprocessable("The URL must start with https:// or http://.")
    wanted = sorted(set(events))
    unknown = [e for e in wanted if e not in EVENTS]
    if not wanted or unknown:
        raise unprocessable("Choose events from the list.", {"known": list(EVENTS)})
    if event_id is not None and not Event.objects.filter(pk=event_id).exists():
        raise not_found("No such event.")
    if not _may_manage(principal, event_id):
        raise forbidden("Only organizers can register webhooks.")
    secret = secrets.token_hex(24)
    hook = Webhook.objects.create(
        owner_id=principal.user_id,
        event_id=event_id,
        url=url,
        events=wanted,
        secret=secret,
    )
    audit.record(
        "webhook.created",
        f"Webhook registered for {', '.join(wanted)}",
        actor=principal,
        actor_role="organizer",
        event_id=event_id or "",
        target=hook,
        details={"url": url, "events": wanted},
    )
    return hook, secret


def owned(principal: Principal, webhook_id: str) -> Webhook:
    hook = Webhook.objects.filter(pk=webhook_id).first()
    if hook is None or not (principal.is_admin or hook.owner_id == principal.user_id):
        raise not_found("No such webhook.")
    return hook


@transaction.atomic
def delete(principal: Principal, webhook_id: str) -> None:
    hook = owned(principal, webhook_id)
    audit.record(
        "webhook.deleted",
        f"Webhook to {hook.url} removed",
        actor=principal,
        actor_role="organizer",
        event_id=hook.event_id or "",
    )
    hook.delete()


def _matching(event_id: str, name: str) -> list[Webhook]:
    organizers = set(
        RoleGrant.objects.filter(event_id=event_id, role="organizer").values_list(
            "user_id", flat=True
        )
    )
    hooks_ = Webhook.objects.filter(active=True).filter(
        Q(event_id=event_id)
        | Q(event__isnull=True, owner_id__in=organizers)
        | Q(event__isnull=True, owner__is_admin=True)
    )
    return [h for h in hooks_ if name in h.events]


def emit(event_id: str, name: str, data: dict[str, Any]) -> int:
    """Queue a delivery to every matching webhook, inside the caller's transaction."""
    queued = 0
    for hook in _matching(event_id, name):
        delivery = new_id("dlv")
        outbox.enqueue(
            "webhook",
            {
                "webhook": hook.pk,
                "body": {
                    "id": delivery,
                    "event": name,
                    "event_id": event_id,
                    "created_at": clock.now().isoformat(),
                    "data": data,
                },
            },
            dedupe_key=f"webhook:{hook.pk}:{delivery}",
        )
        queued += 1
    return queued


def ping(principal: Principal, webhook_id: str) -> None:
    hook = owned(principal, webhook_id)
    body = {
        "id": new_id("dlv"),
        "event": "ping",
        "event_id": hook.event_id or "",
        "created_at": clock.now().isoformat(),
        "data": {"message": "Raptor Desk webhook test"},
    }
    outbox.enqueue("webhook", {"webhook": hook.pk, "body": body})


def deliver(payload: dict[str, Any]) -> None:
    """Outbox handler: POST the body, signed. Raising makes the outbox retry later."""
    hook = Webhook.objects.filter(pk=payload["webhook"], active=True).first()
    if hook is None:
        return  # removed or paused since the event: nothing to deliver
    body = json.dumps(payload["body"], separators=(",", ":"), sort_keys=True).encode()
    request = urllib.request.Request(  # noqa: S310 (organizer-configured http(s) URL)
        hook.url,
        data=body,
        method="POST",
        headers={
            "Content-Type": "application/json",
            "User-Agent": "RaptorDesk-Webhooks/1",
            SIGNATURE_HEADER: sign(hook.secret, body),
            "X-Raptor-Event": payload["body"]["event"],
            "X-Raptor-Delivery": payload["body"]["id"],
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:  # noqa: S310
            status = f"{response.status}"
    except urllib.error.HTTPError as exc:
        status = f"{exc.code}"
        Webhook.objects.filter(pk=hook.pk).update(last_status=status, last_delivery_at=clock.now())
        raise
    Webhook.objects.filter(pk=hook.pk).update(last_status=status, last_delivery_at=clock.now())


# --- Events emitted from the lifecycle -------------------------------------------------------


@hooks.on_enter(*Phase.values)
def _phase_changed(actor: Principal, event: Event) -> None:
    emit(event.pk, "event.phase_changed", {"phase": event.phase})
    if event.phase == Phase.PUBLISHED:
        emit(event.pk, "results.published", {"results": f"/events/{event.pk}/results"})
