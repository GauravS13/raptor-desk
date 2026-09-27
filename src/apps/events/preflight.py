"""Preflight checks run before an event changes phase.

A blocking check stops the transition unless the organizer overrides it with a
written reason (recorded in the audit trail). Warnings are shown but do not
block. Other apps register their own checks here, for example "projects with
no informative review" before publishing results.
"""

from collections.abc import Callable
from dataclasses import asdict, dataclass

from apps.events.models import Event, Phase

BLOCKING = "blocking"
WARNING = "warning"


@dataclass(frozen=True)
class Check:
    level: str
    code: str
    message: str

    def as_dict(self) -> dict[str, str]:
        return asdict(self)


CheckFn = Callable[[Event], list[Check]]
_CHECKS: dict[str, list[CheckFn]] = {}


def register(*phases: str) -> Callable[[CheckFn], CheckFn]:
    """Run the decorated function before moving an event into any of ``phases``."""

    def decorator(fn: CheckFn) -> CheckFn:
        for phase in phases:
            _CHECKS.setdefault(phase, [])
            if fn not in _CHECKS[phase]:
                _CHECKS[phase].append(fn)
        return fn

    return decorator


def run(event: Event, to_phase: str) -> list[Check]:
    results: list[Check] = []
    for fn in _CHECKS.get(to_phase, []):
        results.extend(fn(event))
    return results


def blocking(checks: list[Check]) -> list[Check]:
    return [check for check in checks if check.level == BLOCKING]


# --- Checks owned by the events app ----------------------------------------


@register(Phase.SUBMISSIONS)
def submission_window_is_set(event: Event) -> list[Check]:
    if event.submissions_close_at is None:
        return [Check(BLOCKING, "no_deadline", "Set the submission deadline first.")]
    return []


@register(Phase.SUBMISSIONS, Phase.JUDGING)
def rubric_is_valid(event: Event) -> list[Check]:
    from apps.events.services import rubric_problems

    return [Check(BLOCKING, "rubric", problem) for problem in rubric_problems(event)]


@register(Phase.JUDGING)
def submissions_are_closed(event: Event) -> list[Check]:
    if event.submissions_are_open:
        return [
            Check(
                BLOCKING, "submissions_open", "Submissions are still open; judging would race them."
            )
        ]
    return []


@register(Phase.JUDGING)
def judges_are_invited(event: Event) -> list[Check]:
    if not event.grants.filter(role="judge").exists():
        return [Check(BLOCKING, "no_judges", "Invite at least one judge before judging starts.")]
    return []
