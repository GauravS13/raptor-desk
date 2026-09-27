"""UI and API parity for state-changing actions.

Every page view that changes state is marked ``@ui_action("name")`` and every
API operation that does the same is marked ``@api_action("name")``. Both call
the same service function. A test fails if any UI action has no API
counterpart, so "every action the UI can take is in the API" holds by
construction rather than by promise.
"""

from collections.abc import Callable
from typing import Any

from django.urls import get_resolver

_UI: dict[str, str] = {}
_API: dict[str, str] = {}


Decorator = Callable[[Callable[..., Any]], Callable[..., Any]]


def _load_views() -> None:
    """Importing the URLconf imports every view module, which registers their actions."""
    get_resolver().url_patterns  # noqa: B018


def _register(table: dict[str, str], name: str) -> Decorator:
    def decorator(fn: Callable[..., Any]) -> Callable[..., Any]:
        table[name] = f"{fn.__module__}.{fn.__qualname__}"
        fn.action_name = name  # type: ignore[attr-defined]
        return fn

    return decorator


def ui_action(name: str) -> Decorator:
    return _register(_UI, name)


def api_action(name: str) -> Decorator:
    return _register(_API, name)


def ui_actions() -> dict[str, str]:
    _load_views()
    return dict(_UI)


def api_actions() -> dict[str, str]:
    _load_views()
    return dict(_API)


def parity_gaps() -> list[str]:
    """UI actions with no API operation. Must be empty."""
    return sorted(set(ui_actions()) - set(api_actions()))
