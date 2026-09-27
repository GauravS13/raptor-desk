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
    path(
        "o/events/<str:event_id>/deliberation",
        views.organizer_deliberation,
        name="org-deliberation",
    ),
    path(
        "o/events/<str:event_id>/deliberation/decisions",
        views.organizer_decide,
        name="org-decide",
    ),
    path("o/events/<str:event_id>/results/freeze", views.organizer_freeze, name="org-freeze"),
    path("events/<str:event_id>/results", views.public_results, name="public-results"),
    path("judge/protocols/<str:code>", views.protocol_page, name="protocol"),
    path("judge/protocols/<str:code>.json", views.protocol_json, name="protocol-json"),
    path("verify", views.verify_form, name="verify"),
    path("verify/<str:code>", views.verify_page, name="verify-record"),
    path("judge/passport", views.passport_toggle, name="passport-toggle"),
    path("judges/<str:judge_id>/passport", views.passport_page, name="passport"),
    path("projects/<str:project_id>/feedback", views.feedback_page, name="feedback"),
    path("projects/<str:project_id>/queries", views.feedback_query, name="feedback-query"),
    path("o/events/<str:event_id>/queries", views.organizer_queries, name="org-queries"),
    path(
        "o/events/<str:event_id>/queries/<str:query_id>/answer",
        views.organizer_answer,
        name="org-answer",
    ),
    path(
        "o/events/<str:event_id>/results/snapshots/<int:number>.json",
        views.organizer_snapshot,
        name="org-snapshot",
    ),
    path("o/events/<str:event_id>/exports", views.organizer_exports, name="org-exports"),
    path(
        "o/events/<str:event_id>/exports/<str:kind>.csv",
        views.organizer_export_csv,
        name="org-export-csv",
    ),
]
