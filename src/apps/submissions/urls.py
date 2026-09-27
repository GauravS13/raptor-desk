from django.urls import path

from apps.submissions import views

urlpatterns = [
    path("events/<str:event_id>", views.event_page, name="event-page"),
    path("events/<str:event_id>/projects", views.gallery_page, name="gallery"),
    path("events/<str:event_id>/submit", views.submit_page, name="submit"),
    path("projects/<str:project_id>", views.project_page, name="project"),
    path("projects/<str:project_id>/withdraw", views.withdraw, name="project-withdraw"),
]
