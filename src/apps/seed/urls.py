from django.urls import path

from apps.seed import views

urlpatterns = [
    path("tour", views.tour, name="tour"),
]
