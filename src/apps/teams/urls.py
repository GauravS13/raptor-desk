from django.urls import path

from apps.teams import views

urlpatterns = [
    path("events/<str:event_id>/team", views.my_team, name="my-team"),
    path("teams/<str:team_id>/invites", views.create_invite, name="team-invite"),
    path("teams/<str:team_id>/leave", views.leave, name="team-leave"),
    path("join/<str:token>", views.join, name="team-join"),
]
