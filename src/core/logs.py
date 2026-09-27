"""Structured logs: one JSON object per line, ready for any log store.

Requests are logged by their route pattern (``/join/<str:token>``), never by
the raw path, so invite and sign-in tokens carried in URLs never reach the
logs. People appear by user id only. Other log lines that quote a path (such
as Django's report of a server error) pass through :class:`RedactSecrets`.
"""

import json
import logging
import re
import time
import uuid
from collections.abc import Callable
from datetime import UTC, datetime

from django.http import HttpRequest, HttpResponse

# URL prefixes whose next path segment is a secret token. A test fails if a
# route with a token parameter is added without being listed here.
SECRET_ROUTE_PREFIXES = ("join", "login/magic", "api/invites")

_SECRET_PATH = re.compile(
    r"(/(?:" + "|".join(re.escape(p) for p in SECRET_ROUTE_PREFIXES) + r")/)[^/?#\s'\"]+"
)
_REQUEST_ID = re.compile(r"^[A-Za-z0-9._-]{1,64}$")
_RESERVED = set(vars(logging.makeLogRecord({}))) | {"message", "asctime", "request"}

logger = logging.getLogger("raptor.request")


def redact(text: str) -> str:
    return _SECRET_PATH.sub(r"\1[redacted]", text)


class RedactSecrets(logging.Filter):
    """Remove URL tokens from any log message before it is written."""

    def filter(self, record: logging.LogRecord) -> bool:
        message = record.getMessage()
        cleaned = redact(message)
        if cleaned != message:
            record.msg, record.args = cleaned, None
        return True


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        entry: dict[str, object] = {
            "ts": datetime.fromtimestamp(record.created, UTC).isoformat(timespec="milliseconds"),
            "level": record.levelname.lower(),
            "logger": record.name,
            "msg": record.getMessage(),
        }
        for key, value in vars(record).items():
            if key not in _RESERVED and not key.startswith("_"):
                entry[key] = value
        if record.exc_info:
            entry["exc"] = redact(self.formatException(record.exc_info))
        return json.dumps(entry, default=str, ensure_ascii=False)


class RequestLogMiddleware:
    """Log one line per request and tag the response with a request id.

    A valid incoming ``X-Request-ID`` is kept, so a proxy's id links its logs
    to ours; otherwise a new id is generated. Docker health probes are logged
    at DEBUG to keep the log readable.
    """

    def __init__(self, get_response: Callable[[HttpRequest], HttpResponse]) -> None:
        self.get_response = get_response

    def __call__(self, request: HttpRequest) -> HttpResponse:
        started = time.perf_counter()
        incoming = request.headers.get("X-Request-ID", "")
        request_id = incoming if _REQUEST_ID.match(incoming) else uuid.uuid4().hex
        request.request_id = request_id  # type: ignore[attr-defined]
        response = self.get_response(request)
        response["X-Request-ID"] = request_id

        match = request.resolver_match
        route = f"/{match.route}" if match is not None else "unmatched"
        principal = getattr(request, "principal", None)
        quiet = route == "/healthz" and response.status_code == 200
        logger.log(
            logging.DEBUG if quiet else logging.INFO,
            "%s %s %s",
            request.method,
            route,
            response.status_code,
            extra={
                "request_id": request_id,
                "method": request.method,
                "route": route,
                "status": response.status_code,
                "duration_ms": round((time.perf_counter() - started) * 1000, 1),
                "user_id": getattr(principal, "user_id", None),
                "auth": getattr(principal, "auth", "anonymous"),
            },
        )
        return response
