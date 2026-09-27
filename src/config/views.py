from django.conf import settings
from django.http import HttpRequest, HttpResponse, JsonResponse
from django.shortcuts import render
from django.views.decorators.http import require_GET

from core import signing
from core.policy import Rule, define, policy

define("public.home", Rule(public=True, description="The start page is public."))


@require_GET
@policy("public.home")
def home(request: HttpRequest) -> HttpResponse:
    from apps.events.models import Event, Phase

    events = Event.objects.exclude(phase=Phase.DRAFT).order_by("-created_at")
    context = {"events": events, "demo_profile": settings.PROFILE == "demo"}
    return render(request, "home.html", context)


define(
    "public.signing_key",
    Rule(public=True, description="Anyone can fetch the public key to verify signed records."),
)


@require_GET
@policy("public.signing_key")
def signing_key(request: HttpRequest) -> JsonResponse:
    return JsonResponse(
        {
            "alg": signing.ALGORITHM,
            "key_id": signing.key_id(),
            "public_key": signing.public_key_hex(),
            "canonical_json": "sorted keys, separators (',', ':'), UTF-8, ensure_ascii=False",
        }
    )
