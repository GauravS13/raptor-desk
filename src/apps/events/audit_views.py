"""The audit trail, readable in the portal: filter by action, actor or text; organizers only."""

from datetime import datetime

from django.core.paginator import Paginator
from django.db.models import Q, QuerySet
from django.http import HttpRequest, HttpResponse
from django.shortcuts import render
from django.views.decorators.http import require_GET
from ninja import Router, Schema

from apps.accounts.models import User
from apps.events import policies
from apps.events.services import get_event
from core.models import AuditEvent
from core.policy import policy

router = Router(tags=["audit"])


def _entries(event_id: str, action: str = "", actor: str = "", q: str = "") -> QuerySet[AuditEvent]:
    entries = AuditEvent.objects.filter(event_id=event_id)
    if action:
        entries = entries.filter(action__startswith=action)
    if actor:
        entries = entries.filter(actor_id=actor)
    if q:
        entries = entries.filter(Q(summary__icontains=q) | Q(target_id__icontains=q))
    return entries.order_by("-at", "-id")


class AuditOut(Schema):
    id: str
    at: datetime
    actor_id: str
    actor: str
    actor_role: str
    action: str
    target_type: str
    target_id: str
    summary: str


def _names(entries: list[AuditEvent]) -> dict[str, str]:
    ids = {e.actor_id for e in entries if e.actor_id}
    return {u.pk: (u.name or u.email) for u in User.objects.filter(pk__in=ids)}


@router.get("/events/{event_id}/audit", response=list[AuditOut])
@policy(policies.EVENTS_MANAGE)
def audit_api(
    request: HttpRequest,
    event_id: str,
    action: str = "",
    actor: str = "",
    q: str = "",
    limit: int = 200,
) -> list[AuditOut]:
    entries = list(_entries(event_id, action, actor, q)[: max(1, min(limit, 1000))])
    names = _names(entries)
    return [
        AuditOut(
            id=e.id,
            at=e.at,
            actor_id=e.actor_id,
            actor=names.get(e.actor_id, "deleted user") if e.actor_id else "system",
            actor_role=e.actor_role,
            action=e.action,
            target_type=e.target_type,
            target_id=e.target_id,
            summary=e.summary,
        )
        for e in entries
    ]


@require_GET
@policy(policies.EVENTS_MANAGE)
def audit_page(request: HttpRequest, event_id: str) -> HttpResponse:
    event = get_event(event_id)
    action = request.GET.get("action", "").strip()
    actor = request.GET.get("actor", "").strip()
    q = request.GET.get("q", "").strip()
    page = Paginator(_entries(event.pk, action, actor, q), 100).get_page(request.GET.get("page"))
    names = _names(list(page))
    rows = [
        {
            "entry": e,
            "who": names.get(e.actor_id, "deleted user") if e.actor_id else "system",
        }
        for e in page
    ]
    actions = (
        AuditEvent.objects.filter(event_id=event.pk)
        .values_list("action", flat=True)
        .distinct()
        .order_by("action")
    )
    context = {
        "event": event,
        "tab": "audit",
        "page": page,
        "rows": rows,
        "action": action,
        "actor": actor,
        "q": q,
        "actions": actions,
    }
    return render(request, "events/audit.html", context)
