"""Outbox handlers owned by core. Apps add their own in ``<app>/handlers.py``."""

from typing import Any

from django.conf import settings
from django.core.mail import EmailMultiAlternatives

from core.outbox import handler


@handler("email")
def send_email(payload: dict[str, Any]) -> None:
    """Payload: {"to": [...], "subject": str, "body": str, "html": optional str}."""
    message = EmailMultiAlternatives(
        subject=payload["subject"],
        body=payload["body"],
        from_email=settings.DEFAULT_FROM_EMAIL,
        to=list(payload["to"]),
    )
    if payload.get("html"):
        message.attach_alternative(payload["html"], "text/html")
    message.send(fail_silently=False)
