from django.urls import path

from apps.integrations import views

urlpatterns = [
    path("embed/events/<str:event_id>/gallery", views.embed_gallery, name="embed-gallery"),
    path(
        "o/events/<str:event_id>/integrations",
        views.organizer_integrations,
        name="org-integrations",
    ),
    path(
        "o/events/<str:event_id>/integrations/webhooks",
        views.organizer_create_webhook,
        name="org-webhook-create",
    ),
    path(
        "o/events/<str:event_id>/integrations/webhooks/<str:webhook_id>/delete",
        views.organizer_delete_webhook,
        name="org-webhook-delete",
    ),
    path(
        "o/events/<str:event_id>/integrations/webhooks/<str:webhook_id>/ping",
        views.organizer_ping_webhook,
        name="org-webhook-ping",
    ),
    path("o/events/<str:event_id>/bundle.json", views.organizer_export, name="org-export"),
    path("o/import", views.organizer_import, name="org-import"),
]
