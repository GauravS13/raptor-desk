from django.http import HttpRequest, HttpResponse
from django.shortcuts import render
from django.views.decorators.http import require_GET

from core.policy import Rule, define, policy

define("public.home", Rule(public=True, description="The start page is public."))


@require_GET
@policy("public.home")
def home(request: HttpRequest) -> HttpResponse:
    return render(request, "home.html")
