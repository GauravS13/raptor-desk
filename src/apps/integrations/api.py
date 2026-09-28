"""Webhooks, event bundles (export and import) over the REST API."""

from datetime import datetime
from typing import Any

from django.http import HttpRequest
from ninja import Field, Router, Schema, Status

from apps.events import policies as event_policies
from apps.events.services import get_event
from apps.integrations import bundles, webhooks
from core.actions import api_action
from core.policy import get_principal, policy

router = Router(tags=["integrations"])


class WebhookIn(Schema):
    url: str
    events: list[str]
    event_id: str | None = None


class WebhookOut(Schema):
    id: str
    url: str
    events: list[str]
    event_id: str | None
    active: bool
    created_at: datetime
    last_status: str


class WebhookCreatedOut(WebhookOut):
    secret: str = Field(..., description="Shown once. Deliveries are signed with it.")


class ImportIn(Schema):
    bundle: dict[str, Any]
    name: str = ""


class ImportOut(Schema):
    event: str
    name: str


def webhook_out(hook: Any) -> WebhookOut:
    return WebhookOut(
        id=hook.pk,
        url=hook.url,
        events=hook.events,
        event_id=hook.event_id,
        active=hook.active,
        created_at=hook.created_at,
        last_status=hook.last_status,
    )


@router.post("/webhooks", response={201: WebhookCreatedOut})
@api_action("webhook.create")
@policy(event_policies.EVENTS_CREATE)
def create_webhook(request: HttpRequest, payload: WebhookIn) -> Status[WebhookCreatedOut]:
    """Register a webhook. Events: comment.created, project.submitted, event.phase_changed,
    results.published, ping. Deliveries carry X-Dogfood-Signature: sha256=<HMAC of the body>."""
    hook, secret = webhooks.create(
        get_principal(request), payload.url, payload.events, payload.event_id
    )
    return Status(201, WebhookCreatedOut(**webhook_out(hook).dict(), secret=secret))


@router.get("/webhooks", response=list[WebhookOut])
@policy(event_policies.EVENTS_CREATE)
def list_webhooks(request: HttpRequest) -> list[WebhookOut]:
    principal = get_principal(request)
    items = (
        webhooks.Webhook.objects.all()
        if principal.is_admin
        else (webhooks.Webhook.objects.filter(owner_id=principal.user_id))
    )
    return [webhook_out(h) for h in items]


@router.delete("/webhooks/{webhook_id}", response={204: None})
@api_action("webhook.delete")
@policy(event_policies.EVENTS_CREATE)
def delete_webhook(request: HttpRequest, webhook_id: str) -> Status[None]:
    webhooks.delete(get_principal(request), webhook_id)
    return Status(204, None)


@router.post("/webhooks/{webhook_id}/ping", response={202: None})
@api_action("webhook.ping")
@policy(event_policies.EVENTS_CREATE)
def ping_webhook(request: HttpRequest, webhook_id: str) -> Status[None]:
    webhooks.ping(get_principal(request), webhook_id)
    return Status(202, None)


@router.get("/events/{event_id}/export")
@policy(event_policies.EVENTS_MANAGE)
def export_event(request: HttpRequest, event_id: str) -> dict[str, Any]:
    """The whole event as one JSON bundle, with manifest.sha256 over the rest."""
    return bundles.export(get_event(event_id))


@router.post("/event-bundles", response={201: ImportOut})
@api_action("event.import")
@policy(event_policies.EVENTS_CREATE)
def import_event(request: HttpRequest, payload: ImportIn) -> Status[ImportOut]:
    """Create a new event from a bundle. A bundle changed after export is refused (422)."""
    event = bundles.import_bundle(get_principal(request), payload.bundle, payload.name)
    return Status(201, ImportOut(event=event.pk, name=event.name))
