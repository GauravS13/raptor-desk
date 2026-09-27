"""Builds the request's Principal from a bearer token or a browser session.

The REST API (/api/...) accepts bearer tokens only. Django-ninja views are
CSRF-exempt, so honouring the session cookie there would let another site
make requests on a signed-in user's behalf. Pages accept the session cookie
and are CSRF-protected by Django's middleware.
"""

from collections.abc import Callable

from django.http import HttpRequest, HttpResponse

from apps.accounts.models import ApiToken, User
from core.policy import ANONYMOUS, Principal, is_api_request


def load_grants(user: User) -> dict[str, frozenset[str]]:
    """Per-event roles for ``user``. Filled in once role grants exist."""
    grants: dict[str, set[str]] = {}
    for event_id, role in _grant_rows(user):
        grants.setdefault(event_id, set()).add(role)
    return {event_id: frozenset(roles) for event_id, roles in grants.items()}


def _grant_rows(user: User) -> list[tuple[str, str]]:
    return []


def principal_for(user: User, auth: str) -> Principal:
    return Principal(
        user_id=user.pk,
        email=user.email,
        is_admin=user.is_admin,
        auth=auth,
        grants=load_grants(user),
    )


def build_principal(request: HttpRequest) -> Principal:
    header = request.META.get("HTTP_AUTHORIZATION", "")
    scheme, _, credential = header.partition(" ")
    if scheme.lower() == "bearer" and credential.strip():
        user = ApiToken.authenticate(credential.strip())
        return principal_for(user, "bearer") if user else ANONYMOUS
    if not is_api_request(request) and request.user.is_authenticated:
        return principal_for(request.user, "session")  # type: ignore[arg-type]
    return ANONYMOUS


class PrincipalMiddleware:
    """Attach ``request.principal``. Must run after AuthenticationMiddleware."""

    def __init__(self, get_response: Callable[[HttpRequest], HttpResponse]) -> None:
        self.get_response = get_response

    def __call__(self, request: HttpRequest) -> HttpResponse:
        request.principal = build_principal(request)  # type: ignore[attr-defined]
        return self.get_response(request)
