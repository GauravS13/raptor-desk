"""Community voting and comments over the REST API."""

from datetime import datetime
from typing import Any

from django.http import HttpRequest
from ninja import Field, Router, Schema, Status

from apps.events import policies as event_policies
from apps.events.services import get_event
from apps.voting import comments, services
from apps.voting.models import BallotToken, VoteStatus
from core.actions import api_action
from core.policy import Rule, define, get_principal, policy

router = Router(tags=["voting"])

PUBLIC_BALLOT = define(
    "voting.public",
    Rule(public=True, description="Ballot links, email-link requests and public results."),
)
SIGNED_IN = define(
    "voting.signed_in",
    Rule(authenticated=True, description="Voting with your account, and commenting."),
)


class MintIn(Schema):
    count: int = Field(..., ge=1, le=services.MAX_MINT)


class MintOut(Schema):
    tokens: list[str]
    links: list[str]


class BallotProjectOut(Schema):
    id: str
    name: str
    tagline: str


class BallotOut(Schema):
    event_id: str
    ballot_id: str
    scheme: str
    credits: int
    remaining: int
    voted: bool
    projects: list[BallotProjectOut]


class VoteIn(Schema):
    project: str
    credits: int = 1


class VoteOut(Schema):
    status: str


class EmailIn(Schema):
    email: str


class TallyRowOut(Schema):
    project_id: str
    name: str
    votes: int
    weight: float
    held: int | None = None


class CommunityResultsOut(Schema):
    event_id: str
    voting_open: bool
    scheme: str
    projects: list[TallyRowOut]


class ReviewIn(Schema):
    vote_ids: list[str]
    accept: bool


class ReviewOut(Schema):
    updated: int


class HeldVoteOut(Schema):
    id: str
    project_id: str
    ballot_id: str
    created_at: datetime


class CommentIn(Schema):
    body: str


class CommentOut(Schema):
    id: str
    project_id: str
    author: str
    body: str
    created_at: datetime


def ballot_out(token: BallotToken) -> BallotOut:
    current = services.state(token)
    return BallotOut(
        event_id=token.event_id,
        ballot_id=token.pk,
        scheme=token.event.voting_scheme,
        credits=current.credits,
        remaining=current.remaining,
        voted=bool(current.voted_for),
        projects=[
            BallotProjectOut(
                id=p.pk,
                name=p.canonical_version.name if p.canonical_version else p.pk,
                tagline=p.canonical_version.tagline if p.canonical_version else "",
            )
            for p in services.ballot_projects(token)
        ],
    )


def comment_out(item: Any) -> CommentOut:
    return CommentOut(
        id=item.pk,
        project_id=item.project_id,
        author=item.author.name or "participant",
        body=item.body,
        created_at=item.created_at,
    )


# The acknowledgement a voter gets. Deliberately the same whether the vote was
# counted or held for review, and never with a count, rank or score in it.
RECORDED = VoteOut(status="recorded")


@router.post("/events/{event_id}/ballot-tokens", response={201: MintOut})
@api_action("voting.mint")
@policy(event_policies.EVENTS_MANAGE)
def mint(request: HttpRequest, event_id: str, payload: MintIn) -> Status[MintOut]:
    """Mint ballot links to hand out. The tokens are shown once and never stored."""
    raws = services.mint_links(get_principal(request), get_event(event_id), payload.count)
    return Status(201, MintOut(tokens=raws, links=[services.ballot_link(r) for r in raws]))


@router.get("/ballots/{token}", response=BallotOut)
@policy(PUBLIC_BALLOT)
def get_ballot(request: HttpRequest, token: str) -> BallotOut:
    """This voter's ballot, in their own stable random order, without their own team."""
    return ballot_out(services.resolve(request, token))


@router.post("/ballots/{token}/vote", response={201: VoteOut})
@api_action("voting.vote")
@policy(PUBLIC_BALLOT)
def vote(request: HttpRequest, token: str, payload: VoteIn) -> Status[VoteOut]:
    services.cast(request, services.resolve(request, token), payload.project, payload.credits)
    return Status(201, RECORDED)


@router.get("/events/{event_id}/ballot", response=BallotOut)
@policy(SIGNED_IN)
def my_ballot(request: HttpRequest, event_id: str) -> BallotOut:
    return ballot_out(services.member_ballot(get_principal(request), get_event(event_id)))


@router.post("/events/{event_id}/votes", response={201: VoteOut})
@api_action("voting.vote_member")
@policy(SIGNED_IN)
def vote_as_member(request: HttpRequest, event_id: str, payload: VoteIn) -> Status[VoteOut]:
    """Vote with your account. You cannot vote for your own team's project."""
    token = services.member_ballot(get_principal(request), get_event(event_id))
    services.cast(request, token, payload.project, payload.credits)
    return Status(201, RECORDED)


@router.post("/events/{event_id}/vote-links", response={202: VoteOut})
@api_action("voting.email_link")
@policy(PUBLIC_BALLOT)
def request_link(request: HttpRequest, event_id: str, payload: EmailIn) -> Status[VoteOut]:
    """Email a one-off ballot link. The same person (after normalising) gets only one."""
    services.request_email_link(request, get_event(event_id), payload.email)
    return Status(202, VoteOut(status="sent"))


@router.get("/events/{event_id}/community-results", response=CommunityResultsOut)
@policy(PUBLIC_BALLOT)
def community_results(request: HttpRequest, event_id: str) -> CommunityResultsOut:
    """Vote tallies: organizers any time, everyone else once voting has closed."""
    event = get_event(event_id)
    principal = get_principal(request)
    services.require_results_visible(principal, event)
    organizer = principal.is_admin or principal.has_role("organizer", event.pk)
    return CommunityResultsOut(
        event_id=event.pk,
        voting_open=event.voting_is_open,
        scheme=event.voting_scheme,
        projects=[
            TallyRowOut(
                project_id=row.project_id,
                name=row.name,
                votes=row.counted,
                weight=row.weight,
                held=row.held if organizer else None,
            )
            for row in services.tally(event)
        ],
    )


@router.get("/events/{event_id}/votes/held", response=list[HeldVoteOut])
@policy(event_policies.EVENTS_MANAGE)
def held_votes(request: HttpRequest, event_id: str) -> list[HeldVoteOut]:
    items = get_event(event_id).votes.filter(status=VoteStatus.QUARANTINED).order_by("created_at")
    return [
        HeldVoteOut(id=v.pk, project_id=v.project_id, ballot_id=v.token_id, created_at=v.created_at)
        for v in items
    ]


@router.post("/events/{event_id}/votes/review", response=ReviewOut)
@api_action("voting.review")
@policy(event_policies.EVENTS_MANAGE)
def review(request: HttpRequest, event_id: str, payload: ReviewIn) -> ReviewOut:
    updated = services.review_held(
        get_principal(request), get_event(event_id), payload.vote_ids, payload.accept
    )
    return ReviewOut(updated=updated)


@router.get("/projects/{project_id}/comments", response=list[CommentOut])
@policy(PUBLIC_BALLOT)
def list_comments(request: HttpRequest, project_id: str) -> list[CommentOut]:
    return [comment_out(c) for c in comments.visible(comments.commentable(project_id))]


@router.post("/projects/{project_id}/comments", response={201: CommentOut})
@api_action("comment.create")
@policy(SIGNED_IN)
def add_comment(request: HttpRequest, project_id: str, payload: CommentIn) -> Status[CommentOut]:
    item = comments.post(get_principal(request), project_id, payload.body)
    return Status(201, comment_out(item))


@router.delete("/events/{event_id}/comments/{comment_id}", response=CommentOut)
@api_action("comment.hide")
@policy(event_policies.EVENTS_MANAGE)
def hide_comment(request: HttpRequest, event_id: str, comment_id: str) -> CommentOut:
    return comment_out(comments.hide(get_principal(request), event_id, comment_id))
