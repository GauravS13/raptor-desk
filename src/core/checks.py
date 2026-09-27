"""System check: every route must declare an access policy.

The entrypoint runs ``manage.py check`` before serving, so a route without a
policy stops the portal from booting instead of shipping unguarded.
"""

import functools
from collections.abc import Iterator
from typing import Any

from django.core.checks import CheckMessage, Error, Tags, register
from django.urls import URLPattern, URLResolver, get_resolver

from core.policy import iter_api_operations, registry

# Routes guarded by something other than our policy layer.
EXEMPT_PREFIXES = ("admin/",)  # Django admin: staff-only, its own login.
EXEMPT_MODULE_PREFIXES = ("ninja.",)  # Ninja dispatchers; operations are checked separately.


def iter_url_patterns(
    patterns: list[URLPattern | URLResolver], prefix: str = ""
) -> Iterator[tuple[str, Any]]:
    for entry in patterns:
        route = prefix + str(entry.pattern)
        if isinstance(entry, URLResolver):
            yield from iter_url_patterns(entry.url_patterns, route)
        else:
            yield route, entry.callback


def _module_of(callback: Any) -> str:
    target = callback.func if isinstance(callback, functools.partial) else callback
    return getattr(target, "__module__", "") or ""


def unguarded_routes() -> list[str]:
    known = registry()
    problems: list[str] = []
    for route, callback in iter_url_patterns(get_resolver().url_patterns):
        if route.startswith(EXEMPT_PREFIXES):
            continue
        # Ninja dispatchers and its public OpenAPI schema and docs pages.
        if _module_of(callback).startswith(EXEMPT_MODULE_PREFIXES):
            continue
        key = getattr(callback, "policy_key", None)
        if key is None:
            problems.append(f"route '{route}' has no @policy")
        elif key not in known:
            problems.append(f"route '{route}' uses undefined policy '{key}'")
    for methods, path, view_func in iter_api_operations():
        key = getattr(view_func, "policy_key", None)
        if key is None:
            problems.append(f"API {methods} '{path}' has no @policy")
        elif key not in known:
            problems.append(f"API {methods} '{path}' uses undefined policy '{key}'")
    return problems


@register(Tags.security, Tags.urls)
def check_every_route_has_a_policy(app_configs: Any = None, **kwargs: Any) -> list[CheckMessage]:
    return [
        Error(problem, hint="Decorate it with @policy('<key>').", id="raptor.E001")
        for problem in unguarded_routes()
    ]
