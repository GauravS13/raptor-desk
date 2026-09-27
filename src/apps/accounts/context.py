from typing import Any

from django.http import HttpRequest

from core.policy import get_principal


def principal(request: HttpRequest) -> dict[str, Any]:
    """Expose the current principal to every template."""
    return {"principal": get_principal(request)}
