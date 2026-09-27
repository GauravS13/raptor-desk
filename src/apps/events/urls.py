from django.urls import path

from apps.events import audit_views, views

urlpatterns = [
    path("o/", views.org_home, name="org-home"),
    path("o/events/new", views.event_create, name="event-create"),
    path("o/events/<str:event_id>", views.event_overview, name="event-overview"),
    path("o/events/<str:event_id>/phase", views.event_phase, name="event-phase"),
    path("o/events/<str:event_id>/settings", views.event_settings, name="event-settings"),
    path("o/events/<str:event_id>/rubric", views.event_rubric, name="event-rubric"),
    path(
        "o/events/<str:event_id>/rubric/template",
        views.event_apply_template,
        name="event-apply-template",
    ),
    path("o/events/<str:event_id>/structure", views.event_structure, name="event-structure"),
    path("o/events/<str:event_id>/tracks", views.event_add_track, name="event-add-track"),
    path("o/events/<str:event_id>/prizes", views.event_add_prize, name="event-add-prize"),
    path("o/events/<str:event_id>/questions", views.event_add_question, name="event-add-question"),
    path("o/events/<str:event_id>/people", views.event_people, name="event-people"),
    path("o/events/<str:event_id>/audit", audit_views.audit_page, name="event-audit"),
    path(
        "o/events/<str:event_id>/people/<str:user_id>/<str:role>/revoke",
        views.event_revoke,
        name="event-revoke",
    ),
]
