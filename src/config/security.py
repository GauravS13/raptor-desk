from collections.abc import Callable

from django.http import HttpRequest, HttpResponse

# Everything the portal needs is served from its own origin, so the policy
# can forbid inline scripts, inline styles, eval and third-party hosts.
DEFAULT_CSP = "; ".join(
    [
        "default-src 'self'",
        "script-src 'self'",
        "style-src 'self'",
        "img-src 'self' data:",
        "font-src 'self'",
        "connect-src 'self'",
        "object-src 'none'",
        "base-uri 'self'",
        "form-action 'self'",
        "frame-ancestors 'none'",
    ]
)


class ContentSecurityPolicyMiddleware:
    """Adds a strict Content-Security-Policy unless a view already set one."""

    def __init__(self, get_response: Callable[[HttpRequest], HttpResponse]) -> None:
        self.get_response = get_response

    def __call__(self, request: HttpRequest) -> HttpResponse:
        response = self.get_response(request)
        response.headers.setdefault("Content-Security-Policy", DEFAULT_CSP)
        return response
