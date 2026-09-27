"""The single source of "now" for business rules.

Deadlines, voting windows and phase checks read the time from here, so tests
can freeze it without patching Django internals.
"""

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime

from django.utils import timezone

_frozen: datetime | None = None


def now() -> datetime:
    return _frozen if _frozen is not None else timezone.now()


@contextmanager
def frozen(at: datetime) -> Iterator[datetime]:
    """Freeze ``now()`` at ``at`` (must be timezone-aware) for the duration of the block."""
    if timezone.is_naive(at):
        raise ValueError("frozen() needs a timezone-aware datetime")
    global _frozen
    previous = _frozen
    _frozen = at
    try:
        yield at
    finally:
        _frozen = previous
