from django.contrib import admin
from django.urls import path

from config.api import api
from config.health import healthz
from config.views import home

urlpatterns = [
    path("", home, name="home"),
    path("healthz", healthz, name="healthz"),
    path("api/", api.urls),
    path("admin/", admin.site.urls),
]
