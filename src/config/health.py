from django.db import connection
from django.http import HttpRequest, JsonResponse
from django.views.decorators.http import require_GET

from core.policy import Rule, define, policy

define("public.health", Rule(public=True, description="Liveness probe for Docker."))


@require_GET
@policy("public.health")
def healthz(request: HttpRequest) -> JsonResponse:
    """Liveness and database readiness, used by the Docker healthcheck."""
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
            cursor.fetchone()
    except Exception:
        return JsonResponse({"status": "error", "database": "unavailable"}, status=503)
    return JsonResponse({"status": "ok", "database": "ok"})
