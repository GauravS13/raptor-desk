from django.contrib import admin
from django.urls import include, path

from config.api import api
from config.health import healthz
from config.views import home, signing_key

urlpatterns = [
    path("", home, name="home"),
    path("healthz", healthz, name="healthz"),
    path(".well-known/raptor-desk-key", signing_key, name="signing-key"),
    path("", include("apps.accounts.urls")),
    path("", include("apps.events.urls")),
    path("", include("apps.teams.urls")),
    path("", include("apps.submissions.urls")),
    path("", include("apps.judging.urls")),
    path("", include("apps.ops.urls")),
    path("", include("apps.seed.urls")),
    path("", include("apps.voting.urls")),
    path("", include("apps.integrations.urls")),
    path("api/", api.urls),
    path("admin/", admin.site.urls),
]
