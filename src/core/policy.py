"""Default-deny access control for every page and API operation.

Each view or API operation is decorated with ``@policy("some.key")``. The key
names a :class:`Rule` defined next to the view. A system check (see
``core.checks``) refuses to boot the portal if any route lacks a policy, so a
forgotten check cannot silently expose data.

Roles are granted per event (participant, judge, organizer). ``admin`` is the
one global role. Rules are evaluated against the event in the URL
(``event_id``) unless the rule says otherwise.

Denied API requests get a JSON 401 or 403, never a redirect. Denied page
requests redirect anonymous visitors to the login page and show a 403 page to
signed-in users without access.
"""

from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass, field
from enum import Enum
from functools import wraps
from typing import Any
from urllib.parse import urlencode

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.http import HttpRequest, HttpResponse, HttpResponseRedirect
from django.shortcuts import render

from core.http import forbidden, from_api_error, unauthorized

EVENT_ROLES = frozenset({"participant", "judge", "organizer"})
ALL_ROLES = EVENT_ROLES | {"admin"}


@dataclass(frozen=True)
class Principal:
    """Who is making the request, and which roles they hold in which events."""

    user_id: str | None = None
    email: str | None = None
    is_admin: bool = False
    auth: str = "anonymous"  # "anonymous" | "session" | "bearer"
    grants: Mapping[str, frozenset[str]] = field(default_factory=dict)

    @property
    def is_authenticated(self) -> bool:
        return self.user_id is not None

    def roles_in(self, event_id: str) -> frozenset[str]:
        roles = set(self.grants.get(event_id, frozenset()))
        if self.is_admin:
            roles.add("admin")
        return frozenset(roles)

    def has_role(self, role: str, event_id: str | None = None) -> bool:
        if role == "admin":
            return self.is_admin
        if event_id is None:
            return any(role in roles for roles in self.grants.values())
        return role in self.grants.get(event_id, frozenset())

    def events_with_role(self, role: str) -> frozenset[str]:
        return frozenset(event for event, roles in self.grants.items() if role in roles)


ANONYMOUS = Principal()


@dataclass(frozen=True)
class Rule:
    """Who may use a route.

    public:        anyone, including anonymous visitors.
    authenticated: any signed-in user.
    roles:         users holding one of these roles. With ``event_scoped`` the
                   role must be held in the event named by the URL's
                   ``event_id``; without it, in any event.
    machine:       a route for programs outside /api/ (such as /metrics): denials
                   are JSON 401/403, never a redirect to the login page.
    """

    roles: frozenset[str] = frozenset()
    public: bool = False
    authenticated: bool = False
    event_scoped: bool = True
    machine: bool = False
    description: str = ""

    def __post_init__(self) -> None:
        unknown = self.roles - ALL_ROLES
        if unknown:
            raise ImproperlyConfigured(f"Unknown roles in rule: {sorted(unknown)}")
        if self.public and (self.roles or self.authenticated):
            raise ImproperlyConfigured("A public rule cannot also list roles")


class Decision(Enum):
    ALLOW = "allow"
    UNAUTHENTICATED = "unauthenticated"
    FORBIDDEN = "forbidden"


_REGISTRY: dict[str, Rule] = {}


def define(key: str, rule: Rule) -> str:
    """Register a rule under ``key``. Redefining a key with a different rule is an error."""
    existing = _REGISTRY.get(key)
    if existing is not None and existing != rule:
        raise ImproperlyConfigured(f"Policy {key!r} is already defined differently")
    _REGISTRY[key] = rule
    return key


def rule_for(key: str) -> Rule:
    try:
        return _REGISTRY[key]
    except KeyError as exc:
        raise ImproperlyConfigured(f"No policy is defined for {key!r}") from exc


def registry() -> dict[str, Rule]:
    return dict(_REGISTRY)


def decide(rule: Rule, principal: Principal, event_id: str | None = None) -> Decision:
    if rule.public:
        return Decision.ALLOW
    if not principal.is_authenticated:
        return Decision.UNAUTHENTICATED
    if rule.authenticated:
        return Decision.ALLOW
    if "admin" in rule.roles and principal.is_admin:
        return Decision.ALLOW
    wanted = rule.roles - {"admin"}
    if rule.event_scoped:
        if event_id is None:
            return Decision.FORBIDDEN
        held = principal.grants.get(event_id, frozenset())
    else:
        held = frozenset().union(*principal.grants.values()) if principal.grants else frozenset()
    return Decision.ALLOW if wanted & held else Decision.FORBIDDEN


def get_principal(request: HttpRequest) -> Principal:
    return getattr(request, "principal", ANONYMOUS)


def is_api_request(request: HttpRequest) -> bool:
    return request.path.startswith("/api/")


def deny(request: HttpRequest, decision: Decision, *, machine: bool = False) -> HttpResponse:
    if machine or is_api_request(request):
        error = unauthorized() if decision is Decision.UNAUTHENTICATED else forbidden()
        return from_api_error(error)
    if decision is Decision.UNAUTHENTICATED:
        query = urlencode({"next": request.get_full_path()})
        return HttpResponseRedirect(f"{settings.LOGIN_URL}?{query}")
    return render(request, "403.html", status=403)


def policy(key: str) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """Guard a Django view or ninja operation with the rule registered under ``key``."""

    def decorator(view: Callable[..., Any]) -> Callable[..., Any]:
        @wraps(view)
        def wrapper(request: HttpRequest, *args: Any, **kwargs: Any) -> Any:
            rule = rule_for(key)
            decision = decide(rule, get_principal(request), kwargs.get("event_id"))
            if decision is not Decision.ALLOW:
                return deny(request, decision, machine=rule.machine)
            return view(request, *args, **kwargs)

        wrapper.policy_key = key  # type: ignore[attr-defined]
        return wrapper

    return decorator


def require(request: HttpRequest, key: str, event_id: str | None = None) -> None:
    """Inline check for code paths that are not a whole view. Raises ApiError on denial."""
    decision = decide(rule_for(key), get_principal(request), event_id)
    if decision is Decision.UNAUTHENTICATED:
        raise unauthorized()
    if decision is Decision.FORBIDDEN:
        raise forbidden()


# Ninja APIs register themselves here so the boot check can inspect their operations.
_APIS: list[Any] = []


def register_api(api: Any) -> Any:
    if api not in _APIS:
        _APIS.append(api)
    return api


def iter_api_operations() -> Iterator[tuple[str, str, Callable[..., Any]]]:
    """Yield (method list, path, view function) for every operation of every registered API."""

    def walk(router: Any, prefix: str) -> Iterator[tuple[str, str, Callable[..., Any]]]:
        for path, path_view in router.path_operations.items():
            for operation in path_view.operations:
                methods = ",".join(operation.methods)
                yield methods, f"{prefix}{path}", operation.view_func
        for sub_prefix, sub_router in getattr(router, "_routers", []):
            yield from walk(sub_router, f"{prefix}{sub_prefix}")

    for api in _APIS:
        for prefix, router in api._routers:
            yield from walk(router, prefix)
