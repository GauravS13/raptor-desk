import pytest
from django.db import DatabaseError, transaction
from django.test import RequestFactory

from apps.accounts.models import User
from core import audit
from core.models import AuditEvent
from core.policy import Principal


@pytest.mark.django_db
def test_record_stores_readable_entry_without_personal_data() -> None:
    user = User.objects.create_user("org@example.org")
    request = RequestFactory().get("/", REMOTE_ADDR="203.0.113.7")
    entry = audit.record(
        "event.created",
        "Created event Sample Hack 2026",
        actor=Principal(user_id=user.pk, email=user.email),
        actor_role="organizer",
        event_id="evt_01",
        target=user,
        details={"name": "Sample Hack 2026"},
        request=request,
    )
    assert entry.id.startswith("aud_")
    assert entry.actor_id == user.pk
    assert entry.target_type == "accounts.user"
    assert "org@example.org" not in entry.summary
    assert "203.0.113.7" not in entry.ip_hash and len(entry.ip_hash) == 64


@pytest.mark.django_db
def test_audit_trail_is_append_only_at_the_database_level() -> None:
    entry = audit.record("test.action", "Something happened")
    with pytest.raises(DatabaseError), transaction.atomic():
        AuditEvent.objects.filter(pk=entry.pk).update(summary="rewritten history")
    with pytest.raises(DatabaseError), transaction.atomic():
        AuditEvent.objects.filter(pk=entry.pk).delete()
    assert AuditEvent.objects.get(pk=entry.pk).summary == "Something happened"
