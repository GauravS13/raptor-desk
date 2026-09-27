from datetime import timedelta
from typing import Any

import pytest
from django.core import mail
from django.core.management import call_command

from core import clock, outbox
from core.handlers import send_email  # noqa: F401  (registers the email handler)
from core.models import OutboxMessage

calls: list[dict[str, Any]] = []


@outbox.handler("test.ok")
def ok_handler(payload: dict[str, Any]) -> None:
    calls.append(payload)


@outbox.handler("test.fail")
def failing_handler(payload: dict[str, Any]) -> None:
    raise RuntimeError("smtp down")


@pytest.mark.django_db
def test_due_message_is_delivered_once() -> None:
    calls.clear()
    message = outbox.enqueue("test.ok", {"n": 1})
    assert outbox.process_due() == 1
    assert outbox.process_due() == 0
    message.refresh_from_db()
    assert message.status == "done"
    assert calls == [{"n": 1}]


@pytest.mark.django_db
def test_dedupe_key_queues_a_logical_message_only_once() -> None:
    first = outbox.enqueue("test.ok", {"n": 1}, dedupe_key="invite:usr_1")
    second = outbox.enqueue("test.ok", {"n": 2}, dedupe_key="invite:usr_1")
    assert first.pk == second.pk
    assert OutboxMessage.objects.count() == 1


@pytest.mark.django_db
def test_failures_back_off_and_eventually_stop() -> None:
    message = outbox.enqueue("test.fail", {})
    start = clock.now()
    for attempt in range(1, outbox.MAX_ATTEMPTS + 1):
        with clock.frozen(start + timedelta(days=attempt)):
            assert outbox.process_due() == 0
        message.refresh_from_db()
        assert message.attempts == attempt
        assert "smtp down" in message.last_error
    assert message.status == "failed"
    with clock.frozen(start + timedelta(days=99)):
        assert outbox.process_due() == 0


@pytest.mark.django_db
def test_failed_message_waits_for_its_backoff() -> None:
    outbox.enqueue("test.fail", {})
    now = clock.now()
    with clock.frozen(now):
        outbox.process_due()
    message = OutboxMessage.objects.get()
    assert message.next_attempt_at == now + timedelta(seconds=outbox.backoff_seconds(1))


@pytest.mark.django_db
def test_email_handler_sends_through_django_mail() -> None:
    outbox.enqueue(
        "email",
        {"to": ["judge@example.org"], "subject": "You are invited", "body": "Hello"},
    )
    call_command("run_outbox", "--once")
    assert len(mail.outbox) == 1
    assert mail.outbox[0].to == ["judge@example.org"]
    assert mail.outbox[0].subject == "You are invited"


@pytest.mark.django_db
def test_unknown_kind_is_retried_not_lost() -> None:
    outbox.enqueue("nobody.handles.this", {})
    outbox.process_due()
    message = OutboxMessage.objects.get()
    assert message.status == "pending"
    assert "no handler" in message.last_error
