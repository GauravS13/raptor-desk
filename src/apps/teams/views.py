from django import forms
from django.contrib import messages
from django.http import HttpRequest, HttpResponse, HttpResponseRedirect
from django.shortcuts import render
from django.views.decorators.http import require_http_methods, require_POST

from apps.events.api import can_see
from apps.events.services import get_event
from apps.teams import services
from apps.teams.api import TEAMS_SELF
from core.actions import ui_action
from core.http import ApiError, not_found
from core.policy import get_principal, policy


class TeamForm(forms.Form):
    name = forms.CharField(max_length=120, label="Team name")


@require_http_methods(["GET", "POST"])
@ui_action("team.create")
@policy(TEAMS_SELF)
def my_team(request: HttpRequest, event_id: str) -> HttpResponse:
    principal = get_principal(request)
    event = get_event(event_id)
    if not can_see(principal, event):
        raise not_found("No such event.")
    team = services.team_of(principal, event)
    form = TeamForm(request.POST or None)
    if request.method == "POST" and team is None and form.is_valid():
        try:
            services.create_team(principal, event, form.cleaned_data["name"])
            messages.success(request, "Team created. Share an invite link with your teammates.")
            return HttpResponseRedirect(f"/events/{event.pk}/team")
        except ApiError as exc:
            form.add_error(None, exc.message)
    context = {
        "event": event,
        "team": team,
        "form": form,
        "members": team.members.select_related("user") if team else [],
        "is_lead": bool(
            team and team.members.filter(user_id=principal.user_id, role="lead").exists()
        ),
        "formation_open": services.formation_is_open(event),
        "invite_url": request.session.pop("invite_url", None),
    }
    return render(request, "teams/my_team.html", context)


@require_POST
@ui_action("team.invite")
@policy(TEAMS_SELF)
def create_invite(request: HttpRequest, team_id: str) -> HttpResponse:
    team = services.get_team(team_id)
    try:
        _, _, url = services.create_invite(get_principal(request), team)
        request.session["invite_url"] = url  # shown once on the next page
    except ApiError as exc:
        messages.error(request, exc.message)
    return HttpResponseRedirect(f"/events/{team.event_id}/team")


@require_http_methods(["GET", "POST"])
@ui_action("team.join")
@policy(TEAMS_SELF)
def join(request: HttpRequest, token: str) -> HttpResponse:
    try:
        invite = services.find_invite(token)
    except ApiError as exc:
        return render(request, "teams/join_invalid.html", {"message": exc.message}, status=410)
    if request.method == "POST":
        try:
            team = services.join_with_invite(get_principal(request), token)
        except ApiError as exc:
            messages.error(request, exc.message)
            return HttpResponseRedirect(f"/join/{token}")
        messages.success(request, f"You joined {team.name}.")
        return HttpResponseRedirect(f"/events/{team.event_id}/team")
    return render(request, "teams/join.html", {"invite": invite, "token": token})


@require_POST
@ui_action("team.leave")
@policy(TEAMS_SELF)
def leave(request: HttpRequest, team_id: str) -> HttpResponse:
    team = services.get_team(team_id)
    try:
        services.leave_team(get_principal(request), team)
        messages.success(request, "You left the team.")
    except ApiError as exc:
        messages.error(request, exc.message)
    return HttpResponseRedirect(f"/events/{team.event_id}/team")
