from typing import ClassVar

from django.db import models

from core import clock
from core.ids import new_id


def _audit_id() -> str:
    return new_id("aud")


class AuditEvent(models.Model):
    """One line of the audit trail. Append-only: the database rejects UPDATE and DELETE.

    The actor is stored by id only (no copied name or email), so personal data
    stays in one place and an anonymised user shows up as "deleted user".
    """

    id = models.CharField(primary_key=True, max_length=40, default=_audit_id, editable=False)
    at = models.DateTimeField(default=clock.now, db_index=True)
    actor_id = models.CharField(max_length=40, blank=True, db_index=True)
    actor_role = models.CharField(max_length=20, blank=True)
    event_id = models.CharField(max_length=40, blank=True, db_index=True)
    action = models.CharField(max_length=80, db_index=True)
    target_type = models.CharField(max_length=40, blank=True)
    target_id = models.CharField(max_length=40, blank=True, db_index=True)
    summary = models.CharField(max_length=300)
    details = models.JSONField(default=dict, blank=True)
    ip_hash = models.CharField(max_length=64, blank=True)

    class Meta:
        ordering: ClassVar[list[str]] = ["-at", "-id"]

    def __str__(self) -> str:
        return f"{self.at:%Y-%m-%d %H:%M:%S} {self.action}: {self.summary}"
