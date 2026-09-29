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
    from apps.judging.models import Publication

    events = Event.objects.exclude(phase=Phase.DRAFT).order_by("-created_at")
    # A real, published ranking for the hero: public data only, from a signed snapshot.
    publication = (
        Publication.objects.select_related("snapshot", "event").order_by("-published_at").first()
    )
    showcase = None
    if publication is not None:
        payload = publication.snapshot.payload
        showcase = {
            "event": publication.event,
            "rows": payload["ranking"][:5],
            "cutoff": 3 if 3 in payload.get("cutoffs", []) else payload["cutoffs"][0],
            "cutoff_key": str(3 if 3 in payload.get("cutoffs", []) else payload["cutoffs"][0]),
            "hash": publication.snapshot.payload_hash,
            "reviews": payload.get("reviews"),
        }
    context = {
        "events": events,
        "demo_profile": settings.PROFILE == "demo",
        "showcase": showcase,
    }
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
