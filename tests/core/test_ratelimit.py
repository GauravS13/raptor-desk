from datetime import UTC, datetime, timedelta

import pytest

from core import clock, ratelimit
from core.http import ApiError
from core.models import RateBucket

T0 = datetime(2026, 9, 27, 12, 0, 0, tzinfo=UTC)


@pytest.mark.django_db
def test_requests_under_the_limit_pass() -> None:
    with clock.frozen(T0):
        counts = [ratelimit.hit("vote", "1.2.3.4", limit=3, window_seconds=60) for _ in range(3)]
    assert counts == [1, 2, 3]


@pytest.mark.django_db
def test_exceeding_the_limit_raises_429_with_retry_after() -> None:
    with clock.frozen(T0 + timedelta(seconds=15)):
        for _ in range(3):
            ratelimit.hit("vote", "1.2.3.4", limit=3, window_seconds=60)
        with pytest.raises(ApiError) as excinfo:
            ratelimit.hit("vote", "1.2.3.4", limit=3, window_seconds=60)
    assert excinfo.value.status == 429
    assert excinfo.value.details["retry_after"] == 46


@pytest.mark.django_db
def test_new_window_resets_the_count_and_identities_are_separate() -> None:
    with clock.frozen(T0):
        for _ in range(3):
            ratelimit.hit("vote", "1.2.3.4", limit=3, window_seconds=60)
        assert ratelimit.hit("vote", "5.6.7.8", limit=3, window_seconds=60) == 1
    with clock.frozen(T0 + timedelta(seconds=60)):
        assert ratelimit.hit("vote", "1.2.3.4", limit=3, window_seconds=60) == 1


@pytest.mark.django_db
def test_counters_store_no_raw_identity_and_expire() -> None:
    with clock.frozen(T0):
        ratelimit.hit("login", "priya1@example.org", limit=5, window_seconds=300)
    bucket = RateBucket.objects.get()
    assert "priya1" not in bucket.key
    with clock.frozen(T0 + timedelta(days=2)):
        assert ratelimit.purge_expired() == 1
    assert RateBucket.objects.count() == 0
