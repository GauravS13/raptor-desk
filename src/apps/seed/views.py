from django.conf import settings
from django.http import HttpRequest, HttpResponse
from django.shortcuts import render
from django.views.decorators.http import require_GET

from apps.seed.demo import DEMO_PASSWORD, DOGFOOD_EVENT_ID, demo_sign_ins
from core.http import not_found
from core.policy import Rule, define, policy

PUBLIC_TOUR = define(
    "public.tour", Rule(public=True, description="Evaluator tour, demo profile only.")
)


@require_GET
@policy(PUBLIC_TOUR)
def tour(request: HttpRequest) -> HttpResponse:
    """Each DOGFOOD scoring criterion mapped to the live pages that show it."""
    if settings.PROFILE != "demo":
        raise not_found("The tour exists only in the demo profile.")
    context = {
        "sign_ins": demo_sign_ins(),
        "password": DEMO_PASSWORD,
        "fixture_event": "evt_01",
        "dogfood_event": DOGFOOD_EVENT_ID,
    }
    return render(request, "seed/tour.html", context)
