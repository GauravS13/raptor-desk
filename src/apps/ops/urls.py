from django.urls import path

from apps.ops import views

urlpatterns = [
    path("metrics", views.metrics_view, name="metrics"),
]
