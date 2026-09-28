from typing import ClassVar

from django.conf import settings
from django.db import models

from core import clock
from core.ids import new_id


def _webhook_id() -> str:
    return new_id("whk")


class Webhook(models.Model):
    """An HTTPS (or HTTP) endpoint that receives signed JSON when things happen.

    Scoped to one event, or, when ``event`` is empty, to every event its owner
    organizes. The secret is needed to sign deliveries, so it is stored; it is
    shown to the owner once, when the webhook is created.
    """

    id = models.CharField(primary_key=True, max_length=40, default=_webhook_id, editable=False)
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="webhooks"
    )
    event = models.ForeignKey(
        "events.Event", on_delete=models.CASCADE, null=True, blank=True, related_name="webhooks"
    )
    url = models.URLField(max_length=500)
    events = models.JSONField(default=list)
    secret = models.CharField(max_length=64)
    active = models.BooleanField(default=True)
    created_at = models.DateTimeField(default=clock.now)
    last_status = models.CharField(max_length=60, blank=True)
    last_delivery_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering: ClassVar[list[str]] = ["-created_at"]

    def __str__(self) -> str:
        return f"{self.url} ({', '.join(self.events)})"
