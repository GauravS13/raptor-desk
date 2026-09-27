"""Work other apps do when an event enters a phase, inside the same transaction.

For example, entering "published" records which signed results snapshot was
published. A hook that raises rolls the whole phase change back.
"""

from collections.abc import Callable

from apps.events.models import Event
from core.policy import Principal

Hook = Callable[[Principal, Event], None]
_HOOKS: dict[str, list[Hook]] = {}


def on_enter(*phases: str) -> Callable[[Hook], Hook]:
    def decorator(fn: Hook) -> Hook:
        for phase in phases:
            _HOOKS.setdefault(phase, [])
            if fn not in _HOOKS[phase]:
                _HOOKS[phase].append(fn)
        return fn

    return decorator


def entered(actor: Principal, event: Event, phase: str) -> None:
    for fn in _HOOKS.get(phase, []):
        fn(actor, event)
