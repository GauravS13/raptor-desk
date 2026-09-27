from typing import Any

from django.http import HttpRequest

from core.policy import get_principal


def principal(request: HttpRequest) -> dict[str, Any]:
    """Expose the current principal to every template, and which console links to show."""
    current = get_principal(request)
    return {
        "principal": current,
        "nav_judge": bool(current.events_with_role("judge")),
        "nav_organize": current.is_admin or bool(current.events_with_role("organizer")),
    }
