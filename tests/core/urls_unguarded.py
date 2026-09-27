"""A URLconf with a deliberately unguarded view, used to prove the boot check works."""

from django.http import HttpRequest, HttpResponse
from django.urls import path


def forgotten(request: HttpRequest) -> HttpResponse:
    return HttpResponse("oops")


urlpatterns = [path("forgotten", forgotten)]
