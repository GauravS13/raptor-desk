"""Voting and comment pages. Every action here is also in the API (apps.voting.api)."""

from django.contrib import messages
from django.http import HttpRequest, HttpResponse, HttpResponseRedirect
from django.shortcuts import render
from django.views.decorators.http import require_GET, require_http_methods, require_POST

from apps.events import policies as event_policies
from apps.events.services import get_event
from apps.voting import comments, services
from apps.voting.api import PUBLIC_BALLOT, SIGNED_IN
from apps.voting.models import BallotToken, VoteStatus
from core.actions import ui_action
from core.http import ApiError
from core.policy import get_principal, policy


def _render_ballot(
    request: HttpRequest, token: BallotToken, action: str, status: int = 200
) -> HttpResponse:
    context = {
        "event": token.event,
        "token": token,
        "state": services.state(token),
        "projects": services.ballot_projects(token),
        "action": action,
        "quadratic": token.event.voting_scheme == "quadratic",
        "open": token.event.voting_is_open,
    }
    return render(request, "voting/ballot.html", context, status=status)


def _cast_from_form(request: HttpRequest, token: BallotToken) -> str | None:
    try:
        credits = int(request.POST.get("credits") or 1)
    except ValueError:
        credits = 0
    try:
        services.cast(request, token, request.POST.get("project", ""), credits)
    except ApiError as exc:
        if exc.status in (401, 429):
            raise
        return exc.message
    return None


@require_http_methods(["GET", "POST"])
@ui_action("voting.vote")
@policy(PUBLIC_BALLOT)
def ballot_page(request: HttpRequest, token: str) -> HttpResponse:
    ballot = services.resolve(request, token)
    action = f"/vote/{token}"
    if request.method == "POST":
        error = _cast_from_form(request, ballot)
        if error:
            messages.error(request, error)
        else:
            messages.success(request, "Thank you: your vote is recorded.")
        return HttpResponseRedirect(action)
    return _render_ballot(request, ballot, action)


@require_http_methods(["GET", "POST"])
@ui_action("voting.vote_member")
@policy(SIGNED_IN)
def member_ballot_page(request: HttpRequest, event_id: str) -> HttpResponse:
    ballot = services.member_ballot(get_principal(request), get_event(event_id))
    action = f"/events/{event_id}/ballot"
    if request.method == "POST":
        error = _cast_from_form(request, ballot)
        if error:
            messages.error(request, error)
        else:
            messages.success(request, "Thank you: your vote is recorded.")
        return HttpResponseRedirect(action)
    return _render_ballot(request, ballot, action)


@require_POST
@ui_action("voting.email_link")
@policy(PUBLIC_BALLOT)
def email_link(request: HttpRequest, event_id: str) -> HttpResponse:
    try:
        services.request_email_link(request, get_event(event_id), request.POST.get("email", ""))
    except ApiError as exc:
        if exc.status == 429:
            raise
        messages.error(request, exc.message)
    else:
        messages.success(request, "Check your inbox: your personal voting link is on its way.")
    return HttpResponseRedirect(f"/events/{event_id}")


@require_GET
@policy(PUBLIC_BALLOT)
def community_page(request: HttpRequest, event_id: str) -> HttpResponse:
    event = get_event(event_id)
    principal = get_principal(request)
    services.require_results_visible(principal, event)
    context = {
        "event": event,
        "rows": services.tally(event),
        "organizer": principal.is_admin or principal.has_role("organizer", event.pk),
    }
    return render(request, "voting/community.html", context)


@require_GET
@policy(event_policies.EVENTS_MANAGE)
def organizer_voting(request: HttpRequest, event_id: str) -> HttpResponse:
    return _organizer_page(request, event_id)


def _organizer_page(request: HttpRequest, event_id: str, minted: list[str] | None = None):
    event = get_event(event_id)
    held = event.votes.filter(status=VoteStatus.QUARANTINED).select_related("project")
    context = {
        "event": event,
        "tab": "voting",
        "summary": services.summary(event),
        "rows": services.tally(event),
        "held": held.order_by("created_at"),
        "minted": [services.ballot_link(raw) for raw in minted or []],
        "link_channel": services.channel_enabled(event, "link"),
    }
    return render(request, "voting/organizer.html", context)


@require_POST
@ui_action("voting.mint")
@policy(event_policies.EVENTS_MANAGE)
def organizer_mint(request: HttpRequest, event_id: str) -> HttpResponse:
    try:
        count = int(request.POST.get("count") or 0)
    except ValueError:
        count = 0
    raws = services.mint_links(get_principal(request), get_event(event_id), count)
    # Shown once, on this response only: only hashes are stored.
    return _organizer_page(request, event_id, minted=raws)


@require_POST
@ui_action("voting.review")
@policy(event_policies.EVENTS_MANAGE)
def organizer_review(request: HttpRequest, event_id: str) -> HttpResponse:
    accept = request.POST.get("decision") == "count"
    updated = services.review_held(
        get_principal(request), get_event(event_id), request.POST.getlist("vote_ids"), accept
    )
    messages.success(request, f"{'Counted' if accept else 'Rejected'} {updated} held vote(s).")
    return HttpResponseRedirect(f"/o/events/{event_id}/voting")


@require_POST
@ui_action("comment.create")
@policy(SIGNED_IN)
def add_comment(request: HttpRequest, project_id: str) -> HttpResponse:
    try:
        comments.post(get_principal(request), project_id, request.POST.get("body", ""))
    except ApiError as exc:
        if exc.status in (404, 429):
            raise
        messages.error(request, exc.message)
    return HttpResponseRedirect(f"/projects/{project_id}#comments")


@require_POST
@ui_action("comment.hide")
@policy(event_policies.EVENTS_MANAGE)
def hide_comment(request: HttpRequest, event_id: str, comment_id: str) -> HttpResponse:
    comment = comments.hide(get_principal(request), event_id, comment_id)
    messages.success(request, "Comment hidden; it stays in the audit trail.")
    return HttpResponseRedirect(f"/projects/{comment.project_id}#comments")
