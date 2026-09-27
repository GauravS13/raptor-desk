from django.contrib import admin
from django.urls import path

from config.health import healthz
from config.views import home

urlpatterns = [
    path("", home, name="home"),
    path("healthz", healthz, name="healthz"),
    path("admin/", admin.site.urls),
]
