"""Sign-in and token services, shared by the login pages and the REST API."""

import secrets
from datetime import timedelta

from django.conf import settings
from django.contrib.auth import authenticate
from django.db import transaction
from django.http import HttpRequest

from apps.accounts.models import ApiToken, MagicLink, User, normalize_email
from core import audit, clock, outbox, ratelimit
from core.http import unauthorized

MAGIC_LINK_TTL = timedelta(minutes=15)


def password_login(request: HttpRequest, email: str, password: str) -> User | None:
    """Check credentials under per-IP and per-email rate limits."""
    email = normalize_email(email)
    ratelimit.hit("login.ip", ratelimit.client_ip(request), limit=20, window_seconds=300)
    ratelimit.hit("login.email", email, limit=8, window_seconds=300)
    user = authenticate(request, username=email, password=password)
    return user if isinstance(user, User) and user.is_active else None


def send_magic_link(request: HttpRequest, email: str) -> None:
    """Email a one-time sign-in link if the address belongs to an active user.

    The response is identical either way, so the form cannot be used to find
    out which addresses are registered.
    """
    email = normalize_email(email)
    ratelimit.hit("magic.ip", ratelimit.client_ip(request), limit=10, window_seconds=900)
    ratelimit.hit("magic.email", email, limit=3, window_seconds=900)
    user = User.objects.filter(email=email, is_active=True).first()
    if user is None:
        return
    raw = secrets.token_urlsafe(32)
    with transaction.atomic():
        MagicLink.objects.create(
            user=user,
            token_hash=ApiToken.hash(raw),
            expires_at=clock.now() + MAGIC_LINK_TTL,
        )
        link = f"{settings.BASE_URL}/login/magic/{raw}"
        outbox.enqueue(
            "email",
            {
                "to": [user.email],
                "subject": "Your Raptor Desk sign-in link",
                "body": (
                    f"Use this link to sign in to Raptor Desk:\n\n{link}\n\n"
                    "It works once and expires in 15 minutes. "
                    "If you did not ask for it, you can ignore this email."
                ),
            },
        )
        audit.record("auth.magic_link_sent", "Sign-in link emailed", target=user, request=request)


def consume_magic_link(raw: str) -> User | None:
    """Mark the link used and return its user, or None if it is unknown, used or expired."""
    now = clock.now()
    with transaction.atomic():
        link = (
            MagicLink.objects.select_related("user")
            .filter(token_hash=ApiToken.hash(raw), used_at__isnull=True, expires_at__gt=now)
            .first()
        )
        if link is None or not link.user.is_active:
            return None
        claimed = MagicLink.objects.filter(pk=link.pk, used_at__isnull=True).update(used_at=now)
        return link.user if claimed else None


def issue_token_with_password(
    request: HttpRequest, email: str, password: str, label: str
) -> tuple[ApiToken, str]:
    user = password_login(request, email, password)
    if user is None:
        raise unauthorized("Email or password is incorrect.")
    token, raw = ApiToken.issue(user, label or "api")
    audit.record(
        "auth.token_issued", f"API token '{token.label}' issued", target=token, request=request
    )
    return token, raw
