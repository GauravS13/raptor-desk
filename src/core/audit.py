"""Record audit events: who did what, to which object, when, and why.

Entries are written as readable sentences so an organizer can review the trail
in the portal without a database client.
"""

import hashlib
from typing import Any

from django.conf import settings
from django.db import models
from django.http import HttpRequest

from core.models import AuditEvent
from core.policy import Principal


def _ip_hash(request: HttpRequest | None) -> str:
    if request is None:
        return ""
    ip = request.META.get("REMOTE_ADDR", "")
    if not ip:
        return ""
    return hashlib.sha256(f"{settings.SECRET_KEY}:{ip}".encode()).hexdigest()


def record(
    action: str,
    summary: str,
    *,
    actor: Principal | None = None,
    actor_role: str = "",
    event_id: str = "",
    target: models.Model | None = None,
    details: dict[str, Any] | None = None,
    request: HttpRequest | None = None,
) -> AuditEvent:
    return AuditEvent.objects.create(
        actor_id=(actor.user_id or "") if actor else "",
        actor_role=actor_role,
        event_id=event_id,
        action=action,
        target_type=target._meta.label_lower if target is not None else "",
        target_id=str(target.pk) if target is not None else "",
        summary=summary[:300],
        details=details or {},
        ip_hash=_ip_hash(request),
    )
