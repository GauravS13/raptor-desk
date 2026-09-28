from django.urls import path

from apps.voting import views

urlpatterns = [
    path("vote/<str:token>", views.ballot_page, name="ballot"),
    path("events/<str:event_id>/ballot", views.member_ballot_page, name="member-ballot"),
    path("events/<str:event_id>/vote-link", views.email_link, name="vote-link"),
    path("events/<str:event_id>/community", views.community_page, name="community-results"),
    path("o/events/<str:event_id>/voting", views.organizer_voting, name="org-voting"),
    path("o/events/<str:event_id>/voting/mint", views.organizer_mint, name="org-mint"),
    path("o/events/<str:event_id>/voting/review", views.organizer_review, name="org-review"),
    path("projects/<str:project_id>/comments", views.add_comment, name="add-comment"),
    path(
        "o/events/<str:event_id>/comments/<str:comment_id>/hide",
        views.hide_comment,
        name="hide-comment",
    ),
]
