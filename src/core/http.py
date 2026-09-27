"""One error shape for every API response, and no redirects.

    {"error": {"code": "submissions_closed", "message": "...", "details": {...}}}

The acceptance checker follows redirects, so an API that answered an
unauthenticated request with a redirect to a login page would look like a
200 to it. API errors are always a JSON body with a real 4xx status.
"""

from typing import Any

from django.http import JsonResponse


class ApiError(Exception):
    """Raised by services; rendered as a JSON error by the API layer."""

    def __init__(
        self, status: int, code: str, message: str, details: dict[str, Any] | None = None
    ) -> None:
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message
        self.details = details or {}


def error_response(
    status: int, code: str, message: str, details: dict[str, Any] | None = None
) -> JsonResponse:
    body = {"error": {"code": code, "message": message, "details": details or {}}}
    return JsonResponse(body, status=status)


def from_api_error(error: ApiError) -> JsonResponse:
    response = error_response(error.status, error.code, error.message, error.details)
    retry_after = error.details.get("retry_after")
    if error.status == 429 and retry_after is not None:
        response["Retry-After"] = str(retry_after)
    return response


def unauthorized(message: str = "Authentication required.") -> ApiError:
    return ApiError(401, "unauthorized", message)


def forbidden(message: str = "You do not have access to this resource.") -> ApiError:
    return ApiError(403, "forbidden", message)


def not_found(message: str = "Not found.") -> ApiError:
    return ApiError(404, "not_found", message)


def conflict(code: str, message: str, details: dict[str, Any] | None = None) -> ApiError:
    return ApiError(409, code, message, details)


def unprocessable(message: str, details: dict[str, Any] | None = None) -> ApiError:
    return ApiError(422, "invalid", message, details)


def too_many_requests(retry_after: int) -> ApiError:
    return ApiError(
        429, "rate_limited", "Too many requests. Try again later.", {"retry_after": retry_after}
    )
