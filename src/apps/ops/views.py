from django.http import HttpRequest, HttpResponse
from django.views.decorators.http import require_GET

from apps.ops import metrics
from core.policy import Rule, define, policy

OPS_METRICS = define(
    "ops.metrics",
    Rule(
        roles=frozenset({"admin"}),
        event_scoped=False,
        machine=True,
        description="Prometheus metrics: counts and ages only. Admins, by bearer token.",
    ),
)


@require_GET
@policy(OPS_METRICS)
def metrics_view(request: HttpRequest) -> HttpResponse:
    return HttpResponse(metrics.render(), content_type="text/plain; version=0.0.4; charset=utf-8")
