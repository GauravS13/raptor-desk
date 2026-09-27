import json
import re
from datetime import UTC, datetime

import pytest

from core import clock
from core.http import ApiError, from_api_error, too_many_requests
from core.ids import new_id, ulid


def test_ulid_shape_and_uniqueness() -> None:
    values = {ulid() for _ in range(2000)}
    assert len(values) == 2000
    assert all(re.fullmatch(r"[0-9a-hjkmnp-tv-z]{26}", v) for v in values)


def test_ulids_sort_by_creation_time() -> None:
    first = ulid()
    later = [ulid() for _ in range(50)]
    # Same-millisecond ids differ only in their random tail; the time prefix never goes backwards.
    assert all(v[:10] >= first[:10] for v in later)


def test_new_id_uses_prefix() -> None:
    assert new_id("prj").startswith("prj_")
    with pytest.raises(ValueError):
        new_id("Bad-Prefix")


def test_clock_can_be_frozen_and_restores() -> None:
    at = datetime(2026, 3, 1, 18, 0, tzinfo=UTC)
    with clock.frozen(at):
        assert clock.now() == at
    assert clock.now() != at


def test_clock_rejects_naive_datetimes() -> None:
    with pytest.raises(ValueError), clock.frozen(datetime(2026, 1, 1)):
        pass


def test_api_error_renders_uniform_json() -> None:
    error = ApiError(409, "submissions_closed", "Closed.", {"close": "2026-03-01T18:00:00Z"})
    response = from_api_error(error)
    assert response.status_code == 409
    assert json.loads(response.content) == {
        "error": {
            "code": "submissions_closed",
            "message": "Closed.",
            "details": {"close": "2026-03-01T18:00:00Z"},
        }
    }


def test_rate_limit_error_sets_retry_after() -> None:
    response = from_api_error(too_many_requests(30))
    assert response.status_code == 429
    assert response["Retry-After"] == "30"
