from typing import ClassVar

from django.db import models

from core import clock
from core.ids import new_id


def _audit_id() -> str:
    return new_id("aud")


def _outbox_id() -> str:
    return new_id("obx")


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


class OutboxMessage(models.Model):
    """Work to do after a transaction commits: send an email, deliver a webhook, etc.

    Rows are written in the same database transaction as the change that caused
    them, so a message is never lost and never sent for a change that rolled
    back. The worker container processes due rows with retries and backoff.
    """

    id = models.CharField(primary_key=True, max_length=40, default=_outbox_id, editable=False)
    kind = models.CharField(max_length=40, db_index=True)
    payload = models.JSONField(default=dict)
    # NULL (not "") so any number of messages can have no dedupe key under the unique index.
    dedupe_key = models.CharField(max_length=200, null=True, blank=True, unique=True)
    created_at = models.DateTimeField(default=clock.now)
    next_attempt_at = models.DateTimeField(default=clock.now, db_index=True)
    claimed_until = models.DateTimeField(null=True, blank=True)
    attempts = models.PositiveIntegerField(default=0)
    done_at = models.DateTimeField(null=True, blank=True, db_index=True)
    failed_at = models.DateTimeField(null=True, blank=True)
    last_error = models.TextField(blank=True)

    class Meta:
        ordering: ClassVar[list[str]] = ["created_at"]

    def __str__(self) -> str:
        return f"{self.kind} {self.id}"

    @property
    def status(self) -> str:
        if self.done_at:
            return "done"
        if self.failed_at:
            return "failed"
        return "pending"


class RateBucket(models.Model):
    """A fixed-window request counter shared by every web process (no Redis needed)."""

    key = models.CharField(primary_key=True, max_length=200)
    window_start = models.DateTimeField(db_index=True)
    count = models.PositiveIntegerField(default=0)

    def __str__(self) -> str:
        return f"{self.key}={self.count}"
