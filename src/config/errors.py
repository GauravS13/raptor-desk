"""Render service errors raised inside page views as proper HTML error pages.

Services raise ``ApiError`` (for example 404 for an unknown event). The REST
API turns those into JSON; this middleware does the same job for pages, so a
missing object is a 404 page rather than a server error.
"""

from collections.abc import Callable

from django.http import HttpRequest, HttpResponse
from django.shortcuts import render

from core.http import ApiError, from_api_error
from core.policy import is_api_request


class ApiErrorMiddleware:
    def __init__(self, get_response: Callable[[HttpRequest], HttpResponse]) -> None:
        self.get_response = get_response

    def __call__(self, request: HttpRequest) -> HttpResponse:
        return self.get_response(request)

    def process_exception(self, request: HttpRequest, exception: Exception) -> HttpResponse | None:
        if not isinstance(exception, ApiError):
            return None
        if is_api_request(request):
            return from_api_error(exception)
        template = {403: "403.html", 404: "404.html"}.get(exception.status, "error.html")
        return render(request, template, {"error": exception}, status=exception.status)
