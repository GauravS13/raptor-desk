from django.urls import path

from apps.judging import views

urlpatterns = [
    path("judge", views.judge_home, name="judge-home"),
    path("judge/review/<str:assignment_id>", views.review_page, name="judge-review"),
    path("o/events/<str:event_id>/judging", views.organizer_judging, name="org-judging"),
    path("o/events/<str:event_id>/judging/plan", views.organizer_plan, name="org-judging-plan"),
    path(
        "o/events/<str:event_id>/judging/publish",
        views.organizer_publish,
        name="org-judging-publish",
    ),
    path(
        "o/events/<str:event_id>/judging/assign", views.organizer_assign, name="org-judging-assign"
    ),
    path(
        "o/events/<str:event_id>/judging/conflict",
        views.organizer_conflict,
        name="org-judging-conflict",
    ),
    path("o/events/<str:event_id>/results", views.organizer_results, name="org-results"),
    path("o/events/<str:event_id>/close-calls/ask", views.organizer_ask, name="org-ask"),
    path("o/events/<str:event_id>/exports", views.organizer_exports, name="org-exports"),
    path(
        "o/events/<str:event_id>/exports/<str:kind>.csv",
        views.organizer_export_csv,
        name="org-export-csv",
    ),
]
