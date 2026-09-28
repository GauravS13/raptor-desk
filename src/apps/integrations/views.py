"""Integration pages: the embeddable gallery, and the organizer's integrations tab."""

import json

from django.conf import settings
from django.contrib import messages
from django.http import HttpRequest, HttpResponse, HttpResponseRedirect, JsonResponse
from django.shortcuts import render
from django.views.decorators.clickjacking import xframe_options_exempt
from django.views.decorators.http import require_GET, require_POST

from apps.events import policies as event_policies
from apps.events.api import visible_event
from apps.events.services import get_event
from apps.integrations import bundles, webhooks
from apps.submissions import services as submission_services
from config.security import DEFAULT_CSP
from core.actions import ui_action
from core.http import ApiError
from core.policy import Rule, define, get_principal, policy

PUBLIC_EMBED = define(
    "public.embed",
    Rule(public=True, description="The embeddable gallery, meant to be framed by other sites."),
)
# The one page that other sites may frame; every other page forbids it.
EMBED_CSP = DEFAULT_CSP.replace("frame-ancestors 'none'", "frame-ancestors *")


@xframe_options_exempt
@require_GET
@policy(PUBLIC_EMBED)
def embed_gallery(request: HttpRequest, event_id: str) -> HttpResponse:
    event = visible_event(request, event_id)
    context = {
        "event": event,
        "projects": submission_services.gallery(event),
        "base_url": settings.BASE_URL,
    }
    response = render(request, "integrations/embed_gallery.html", context)
    response["Content-Security-Policy"] = EMBED_CSP
    return response


@require_GET
@policy(event_policies.EVENTS_MANAGE)
def organizer_integrations(request: HttpRequest, event_id: str) -> HttpResponse:
    return _integrations_page(request, event_id)


def _integrations_page(
    request: HttpRequest, event_id: str, created: dict[str, str] | None = None
) -> HttpResponse:
    event = get_event(event_id)
    principal = get_principal(request)
    context = {
        "event": event,
        "tab": "integrations",
        "hooks": webhooks.Webhook.objects.filter(event=event),
        "known_events": [e for e in webhooks.EVENTS if e != "ping"],
        "created": created,
        "embed_url": f"{settings.BASE_URL}/embed/events/{event.pk}/gallery",
        "can_manage": principal.is_admin or principal.has_role("organizer", event.pk),
    }
    return render(request, "integrations/organizer.html", context)


@require_POST
@ui_action("webhook.create")
@policy(event_policies.EVENTS_MANAGE)
def organizer_create_webhook(request: HttpRequest, event_id: str) -> HttpResponse:
    try:
        hook, secret = webhooks.create(
            get_principal(request),
            request.POST.get("url", "").strip(),
            request.POST.getlist("events"),
            event_id,
        )
    except ApiError as exc:
        if exc.status in (403, 404):
            raise
        messages.error(request, exc.message)
        return HttpResponseRedirect(f"/o/events/{event_id}/integrations")
    # The secret is shown on this response only.
    return _integrations_page(
        request, event_id, created={"id": hook.pk, "url": hook.url, "secret": secret}
    )


@require_POST
@ui_action("webhook.delete")
@policy(event_policies.EVENTS_MANAGE)
def organizer_delete_webhook(request: HttpRequest, event_id: str, webhook_id: str):
    webhooks.delete(get_principal(request), webhook_id)
    messages.success(request, "Webhook removed.")
    return HttpResponseRedirect(f"/o/events/{event_id}/integrations")


@require_POST
@ui_action("webhook.ping")
@policy(event_policies.EVENTS_MANAGE)
def organizer_ping_webhook(request: HttpRequest, event_id: str, webhook_id: str):
    webhooks.ping(get_principal(request), webhook_id)
    messages.success(request, "Test delivery queued; its status appears here once sent.")
    return HttpResponseRedirect(f"/o/events/{event_id}/integrations")


@require_GET
@policy(event_policies.EVENTS_MANAGE)
def organizer_export(request: HttpRequest, event_id: str) -> HttpResponse:
    event = get_event(event_id)
    response = JsonResponse(bundles.export(event), json_dumps_params={"indent": 1})
    response["Content-Disposition"] = f'attachment; filename="{event.slug}-bundle.json"'
    return response


@require_POST
@ui_action("event.import")
@policy(event_policies.EVENTS_CREATE)
def organizer_import(request: HttpRequest) -> HttpResponse:
    upload = request.FILES.get("bundle")
    try:
        if upload is None:
            raise ApiError(422, "invalid", "Choose a bundle file to import.")
        bundle = json.loads(upload.read().decode("utf-8"))
        event = bundles.import_bundle(get_principal(request), bundle, request.POST.get("name", ""))
    except (ValueError, UnicodeDecodeError):
        messages.error(request, "That file is not a JSON event bundle.")
        return HttpResponseRedirect("/o/")
    except ApiError as exc:
        if exc.status == 403:
            raise
        messages.error(request, exc.message)
        return HttpResponseRedirect("/o/")
    messages.success(request, f"Imported as '{event.name}'.")
    return HttpResponseRedirect(f"/o/events/{event.pk}")
