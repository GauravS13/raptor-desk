"""Community voting: ballots, votes, tallies and the answers to people who cheat.

Channels (an event can enable one, or all):
- **link**: an organizer mints ballot links (to print or hand out). One link, one ballot.
- **email**: a voter asks for a link at their address. The address is
  normalised (trimmed, lowercased, and a "+tag" dropped) and kept only as a
  keyed hash, so a second request for the same person gets no second link.
- **member**: a signed-in person votes with their account; one ballot each.

Rules for every ballot:
- The ballot order is shuffled with the ballot's own seed. It is stable for
  that voter (reloading does not reshuffle it) and different for everyone else.
  The gallery stays in its official order.
- A voter can never vote for their own team's project.
- With one vote per ballot, a second vote is refused. With quadratic voting,
  credits are spread across projects within the ballot's budget, and each
  project's weight is the square root of its credits.
- Voters never see tallies. Organizers see them at any time; everyone else
  only after the voting window closes.

Answers to cheating:
- Failed attempts (unknown or forged ballot links) are rate-limited per network.
  Valid votes are not rate-limited, because a whole venue often shares one address.
- Valid votes arriving in a burst from one network are held for review. The
  voter sees an ordinary confirmation.
- Every accepted, refused and forged attempt is in the audit trail,
  identified by ballot id or a hash prefix, never by email.
"""

import hashlib
import hmac
import math
import random
import secrets
from collections import defaultdict
from dataclasses import dataclass
from datetime import timedelta

from django.conf import settings
from django.db import IntegrityError, transaction
from django.db.models import Sum
from django.http import HttpRequest

from apps.events.models import Event, VotingMode, VotingScheme
from apps.submissions.models import Project
from apps.teams.models import TeamMember
from apps.voting.models import BallotToken, Channel, Vote, VoteStatus
from core import audit, clock, outbox, ratelimit
from core.http import ApiError, conflict, forbidden, not_found, unauthorized, unprocessable
from core.policy import Principal

TOKEN_PREFIX = "tk_"  # noqa: S105  (a prefix, not a secret)
MAX_MINT = 500
BURST_VOTES = 10
BURST_WINDOW = timedelta(seconds=60)
FAILED_LIMIT = 20
FAILED_WINDOW_SECONDS = 60
EMAIL_REQUEST_LIMIT = 10
EMAIL_REQUEST_WINDOW_SECONDS = 600


# --- Tokens -----------------------------------------------------------------------------


def _hash(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()


def normalize_email(email: str) -> str:
    """Lowercase, trim, and drop a '+tag': a+1@x.org and A@X.org are the same voter."""
    email = email.strip().lower()
    local, _, domain = email.partition("@")
    if not local or not domain or "." not in domain:
        raise unprocessable("Enter a valid email address.")
    return f"{local.split('+', 1)[0]}@{domain}"


def email_key(email: str) -> str:
    canonical = normalize_email(email)
    return hmac.new(settings.SECRET_KEY.encode(), canonical.encode(), hashlib.sha256).hexdigest()


def channel_enabled(event: Event, channel: str) -> bool:
    mode = event.voting_mode
    if mode == VotingMode.OFF:
        return False
    wanted = {Channel.LINK: VotingMode.LINK, Channel.EMAIL: VotingMode.EMAIL}.get(
        channel, VotingMode.AUTH
    )
    return mode in (VotingMode.ALL, wanted)


def _require_open(event: Event) -> None:
    if not event.voting_is_open:
        raise conflict("voting_closed", "Voting is not open for this event.")


def _require_channel(event: Event, channel: str) -> None:
    if not channel_enabled(event, channel):
        raise conflict("channel_off", "This way of voting is not enabled for this event.")


def _new_token(event: Event, channel: str, **fields: object) -> tuple[BallotToken, str]:
    raw = f"{TOKEN_PREFIX}{event.pk}.{secrets.token_urlsafe(18)}"
    credits = event.qv_credits if event.voting_scheme == VotingScheme.QUADRATIC else 1
    token = BallotToken.objects.create(
        event=event,
        channel=channel,
        token_hash=_hash(raw),
        seed=secrets.randbits(62),
        credits=credits,
        **fields,
    )
    return token, raw


@transaction.atomic
def mint_links(actor: Principal, event: Event, count: int) -> list[str]:
    """Create ``count`` ballot links. The raw tokens are returned once and never stored."""
    _require_channel(event, Channel.LINK)
    if not 1 <= count <= MAX_MINT:
        raise unprocessable(f"Mint between 1 and {MAX_MINT} ballots at a time.")
    raws = [
        _new_token(event, Channel.LINK, created_by_id=actor.user_id or "")[1] for _ in range(count)
    ]
    audit.record(
        "vote.ballots_minted",
        f"Minted {count} ballot link(s)",
        actor=actor,
        actor_role="organizer",
        event_id=event.pk,
    )
    return raws


def ballot_link(raw: str) -> str:
    return f"{settings.BASE_URL}/vote/{raw}"


def request_email_link(request: HttpRequest, event: Event, email: str) -> None:
    """Email a one-off ballot link. A second request for the same person is refused."""
    _require_open(event)
    _require_channel(event, Channel.EMAIL)
    ratelimit.hit(
        f"vote-email:{event.pk}",
        ratelimit.client_ip(request),
        limit=EMAIL_REQUEST_LIMIT,
        window_seconds=EMAIL_REQUEST_WINDOW_SECONDS,
    )
    key = email_key(email)
    existing = BallotToken.objects.filter(event=event, email_key=key).first()
    if existing is not None:
        audit.record(
            "vote.duplicate_identity",
            f"A second voting link was requested for ballot {existing.pk}; none sent",
            event_id=event.pk,
            target=existing,
            request=request,
        )
        raise conflict(
            "already_sent", "A voting link was already sent to this address. Check your inbox."
        )
    try:
        with transaction.atomic():
            token, raw = _new_token(event, Channel.EMAIL, email_key=key)
    except IntegrityError as exc:  # the same address, requested concurrently
        raise conflict("already_sent", "A voting link was already sent to this address.") from exc
    outbox.enqueue(
        "email",
        {
            "to": [email.strip()],
            "subject": f"Your voting link for {event.name}",
            "body": (
                f"Here is your personal ballot for {event.name}:\n\n{ballot_link(raw)}\n\n"
                "It works once. If you did not ask for it, ignore this email.\n"
            ),
        },
    )
    audit.record(
        "vote.link_sent",
        f"Voting link sent for ballot {token.pk}",
        event_id=event.pk,
        target=token,
        request=request,
    )


def member_ballot(principal: Principal, event: Event) -> BallotToken:
    """The signed-in person's own ballot, created on first use."""
    if not principal.is_authenticated:
        raise unauthorized()
    _require_channel(event, Channel.MEMBER)
    token = BallotToken.objects.filter(
        event=event, user_id=principal.user_id, channel=Channel.MEMBER
    ).first()
    if token is not None:
        return token
    try:
        with transaction.atomic():
            return _new_token(event, Channel.MEMBER, user_id=principal.user_id)[0]
    except IntegrityError:  # created concurrently
        return BallotToken.objects.get(
            event=event, user_id=principal.user_id, channel=Channel.MEMBER
        )


def _event_of(raw: str) -> str:
    if raw.startswith(TOKEN_PREFIX) and "." in raw:
        return raw[len(TOKEN_PREFIX) :].split(".", 1)[0]
    return ""


def resolve(request: HttpRequest, raw: str) -> BallotToken:
    """Find a ballot by its token. Unknown tokens are audited and rate-limited per network."""
    token = BallotToken.objects.select_related("event").filter(token_hash=_hash(raw)).first()
    if token is not None:
        return token
    ratelimit.hit(
        "vote-failed",
        ratelimit.client_ip(request),
        limit=FAILED_LIMIT,
        window_seconds=FAILED_WINDOW_SECONDS,
    )
    event_id = _event_of(raw)
    audit.record(
        "vote.rejected_forged",
        f"Refused an unknown ballot link (hash {_hash(raw)[:12]})",
        event_id=event_id if Event.objects.filter(pk=event_id).exists() else "",
        details={"token_hash_prefix": _hash(raw)[:12]},
        request=request,
    )
    raise not_found("This ballot link is not valid.")


# --- Ballots and votes ---------------------------------------------------------------------


def own_team_ids(token: BallotToken) -> set[str]:
    """Teams the voter belongs to in this event: they cannot vote for those projects."""
    users: set[str] = set()
    if token.user_id:
        users.add(token.user_id)
    if token.email_key:
        members = TeamMember.objects.filter(event=token.event).select_related("user")
        users |= {m.user_id for m in members if email_key(m.user.email) == token.email_key}
    return set(
        TeamMember.objects.filter(event=token.event, user_id__in=users).values_list(
            "team_id", flat=True
        )
    )


def ballot_projects(token: BallotToken) -> list[Project]:
    """This voter's ballot: every eligible project, in the voter's own stable order."""
    projects = list(
        Project.objects.filter(event=token.event, status="submitted")
        .exclude(team_id__in=own_team_ids(token))
        .select_related("canonical_version", "team")
        .order_by("pk")
    )
    random.Random(token.seed).shuffle(projects)  # noqa: S311 (ordering, not security)
    return projects


@dataclass(frozen=True)
class BallotState:
    credits: int
    spent: int
    voted_for: list[str]

    @property
    def remaining(self) -> int:
        return self.credits - self.spent


def state(token: BallotToken) -> BallotState:
    votes = list(token.votes.values_list("project_id", "credits"))
    return BallotState(token.credits, sum(c for _, c in votes), [p for p, _ in votes])


def _ip_hash(request: HttpRequest) -> str:
    ip = ratelimit.client_ip(request)
    return hashlib.sha256(f"{settings.SECRET_KEY}:vote-ip:{ip}".encode()).hexdigest()[:32]


def _refused(request: HttpRequest, token: BallotToken, reason: str, error: ApiError) -> ApiError:
    audit.record(
        "vote.rejected",
        f"Refused a vote from ballot {token.pk}: {reason}",
        event_id=token.event_id,
        target=token,
        details={"reason": reason, "channel": token.channel},
        request=request,
    )
    return error


def _check(token: BallotToken, project: Project, credits: int) -> tuple[str, ApiError] | None:
    if project.team_id in own_team_ids(token):
        return "own team", unprocessable(
            "You cannot vote for your own team's project.", {"code": "own_team"}
        )
    current = state(token)
    if token.event.voting_scheme != VotingScheme.QUADRATIC:
        if current.spent:
            return "ballot already used", conflict(
                "already_voted", "This ballot has already been used."
            )
        return None
    if credits < 1:
        return "no credits given", unprocessable("Spend at least one credit.")
    if project.pk in current.voted_for:
        return "project already voted", conflict(
            "already_voted", "You already voted for this project."
        )
    if credits > current.remaining:
        return "not enough credits", conflict(
            "no_credits", f"You have {current.remaining} credit(s) left."
        )
    return None


def cast(request: HttpRequest, token: BallotToken, project_id: str, credits: int = 1) -> Vote:
    """Record one vote. Refusals are audited after the transaction, so they are never lost."""
    event = token.event
    _require_open(event)
    if event.voting_scheme != VotingScheme.QUADRATIC:
        credits = 1
    refusal: tuple[str, ApiError] | None = None
    with transaction.atomic():
        project = Project.objects.filter(event=event, pk=project_id, status="submitted").first()
        if project is None:
            raise not_found("That project is not on this ballot.")
        refusal = _check(token, project, credits)
        if refusal is None:
            ip_hash = _ip_hash(request)
            recent = Vote.objects.filter(
                event=event, ip_hash=ip_hash, created_at__gte=clock.now() - BURST_WINDOW
            ).count()
            held = recent >= BURST_VOTES
            vote = Vote.objects.create(
                event=event,
                token=token,
                project=project,
                credits=credits,
                status=VoteStatus.QUARANTINED if held else VoteStatus.COUNTED,
                ip_hash=ip_hash,
            )
            audit.record(
                "vote.quarantined" if held else "vote.accepted",
                f"Vote from ballot {token.pk} "
                + ("held for review: a burst from one network" if held else "counted"),
                event_id=event.pk,
                target=vote,
                details={"ballot": token.pk, "channel": token.channel},
                request=request,
            )
    if refusal is not None:
        raise _refused(request, token, *refusal)
    return vote


# --- Tallies ---------------------------------------------------------------------------------


def results_visible_to(principal: Principal, event: Event) -> bool:
    if principal.is_admin or principal.has_role("organizer", event.pk):
        return True
    closed = event.voting_closes_at is not None and clock.now() >= event.voting_closes_at
    return event.voting_mode != VotingMode.OFF and closed


def require_results_visible(principal: Principal, event: Event) -> None:
    if not results_visible_to(principal, event):
        raise forbidden("Voting results stay hidden until the voting window closes.")


@dataclass(frozen=True)
class TallyRow:
    project_id: str
    name: str
    counted: int
    held: int
    weight: float


def tally(event: Event) -> list[TallyRow]:
    """Per-project results. Held (quarantined) votes are shown apart and never counted."""
    counted: dict[str, int] = defaultdict(int)
    held: dict[str, int] = defaultdict(int)
    weight: dict[str, float] = defaultdict(float)
    for project_id, credits, status in Vote.objects.filter(event=event).values_list(
        "project_id", "credits", "status"
    ):
        if status == VoteStatus.COUNTED:
            counted[project_id] += 1
            weight[project_id] += math.sqrt(credits)
        elif status == VoteStatus.QUARANTINED:
            held[project_id] += 1
    projects = Project.objects.filter(event=event, status="submitted").select_related(
        "canonical_version"
    )
    rows = [
        TallyRow(
            project_id=p.pk,
            name=p.canonical_version.name if p.canonical_version else p.pk,
            counted=counted[p.pk],
            held=held[p.pk],
            weight=round(weight[p.pk], 6),
        )
        for p in projects
    ]
    return sorted(rows, key=lambda r: (-r.weight, -r.counted, r.project_id))


@transaction.atomic
def review_held(actor: Principal, event: Event, vote_ids: list[str], accept: bool) -> int:
    held = Vote.objects.filter(event=event, pk__in=vote_ids, status=VoteStatus.QUARANTINED)
    count = held.update(status=VoteStatus.COUNTED if accept else VoteStatus.REJECTED)
    audit.record(
        "vote.review",
        f"{'Counted' if accept else 'Rejected'} {count} held vote(s) after review",
        actor=actor,
        actor_role="organizer",
        event_id=event.pk,
        details={"votes": vote_ids, "accepted": accept},
    )
    return count


def summary(event: Event) -> dict[str, int]:
    votes = Vote.objects.filter(event=event)
    return {
        "ballots": BallotToken.objects.filter(event=event).count(),
        "votes": votes.count(),
        "held": votes.filter(status=VoteStatus.QUARANTINED).count(),
        "credits_spent": votes.aggregate(n=Sum("credits"))["n"] or 0,
    }
