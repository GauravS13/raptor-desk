"""Fixed-window rate limits stored in the database.

Identities (IP addresses, emails, token ids) are hashed with the secret key
before they are stored, so the counter table holds no personal data.
"""

import hashlib
from datetime import UTC, datetime, timedelta

from django.conf import settings
from django.db import transaction
from django.db.models import F
from django.http import HttpRequest

from core import clock
from core.http import too_many_requests
from core.models import RateBucket


def _identity_hash(identity: str) -> str:
    return hashlib.sha256(f"{settings.SECRET_KEY}:{identity}".encode()).hexdigest()[:32]


def hit(scope: str, identity: str, *, limit: int, window_seconds: int) -> int:
    """Count one request. Raises a 429 ApiError once ``limit`` is exceeded in the window."""
    now = clock.now()
    window = int(now.timestamp()) // window_seconds
    window_start = datetime.fromtimestamp(window * window_seconds, tz=UTC)
    key = f"{scope}:{_identity_hash(identity)}:{window}"
    with transaction.atomic():
        RateBucket.objects.get_or_create(key=key, defaults={"window_start": window_start})
        RateBucket.objects.filter(key=key).update(count=F("count") + 1)
        count = RateBucket.objects.values_list("count", flat=True).get(key=key)
    if count > limit:
        window_end = window_start + timedelta(seconds=window_seconds)
        retry_after = max(1, int((window_end - now).total_seconds()) + 1)
        raise too_many_requests(retry_after)
    return count


def client_ip(request: HttpRequest) -> str:
    """The direct peer address. The portal is not deployed behind a trusted proxy by default."""
    return request.META.get("REMOTE_ADDR", "") or "unknown"


def purge_expired(older_than: timedelta = timedelta(days=1)) -> int:
    deleted, _ = RateBucket.objects.filter(window_start__lt=clock.now() - older_than).delete()
    return deleted
