"""Judge score reads (isolation enforced in the repository layer) and CSV exports."""

from datetime import datetime
from typing import Any

from django.http import HttpRequest, HttpResponse
from ninja import Field, Router, Schema, Status

from apps.events import policies as event_policies
from apps.events.services import get_event
from apps.judging import (
    close_calls,
    deliberation,
    exports,
    publishing,
    repositories,
    services,
    snapshots,
)
from apps.judging import results as results_service
from apps.judging.models import Review
from core.actions import api_action
from core.http import not_found
from core.policy import Rule, define, get_principal, policy

JUDGE_SELF = define(
    "judging.self",
    Rule(
        roles=frozenset({"judge"}),
        event_scoped=False,
        description="Judges read their own scores, and nobody else's.",
    ),
)
JUDGE_SCORES = define(
    "judging.judge_scores",
    Rule(
        authenticated=True,
        description="A judge's scores: that judge, the event's organizers or an admin only.",
    ),
)

router = Router(tags=["judging"])


class ScoreOut(Schema):
    criterion: str
    value: int


class ReviewOut(Schema):
    review_id: str
    event_id: str
    project_id: str
    project: str
    version: int
    judge_id: str
    status: str
    scores: list[ScoreOut]
    comment: str
    submitted_at: datetime | None


def review_out(review: Review) -> ReviewOut:
    return ReviewOut(
        review_id=review.pk,
        event_id=review.event_id,
        project_id=review.project_id,
        project=review.version.name,
        version=review.version.n,
        judge_id=review.judge_id,
        status=review.status,
        scores=[
            ScoreOut(criterion=item.criterion.key, value=item.value)
            for item in sorted(review.scores.all(), key=lambda s: s.criterion.order)
        ],
        comment=review.comment,
        submitted_at=review.submitted_at,
    )


@router.get("/judge/scores", response=list[ReviewOut])
@policy(JUDGE_SELF)
def my_scores(request: HttpRequest, event: str = "") -> list[ReviewOut]:
    """The signed-in judge's own reviews and scores."""
    reviews = repositories.reviews_for_self(get_principal(request), event or None)
    return [review_out(review) for review in reviews]


@router.get("/judges/{judge_id}/scores", response=list[ReviewOut])
@policy(JUDGE_SCORES)
def judge_scores(request: HttpRequest, judge_id: str, event: str = "") -> list[ReviewOut]:
    """One judge's scores. Another judge asking gets 403, whatever the event."""
    reviews = repositories.reviews_of_judge(get_principal(request), judge_id, event or None)
    return [review_out(review) for review in reviews]


@router.get("/events/{event_id}/exports/{kind}.csv")
@policy(event_policies.EVENTS_MANAGE)
def export_csv(request: HttpRequest, event_id: str, kind: str) -> HttpResponse:
    """CSV export: results, reviews, assignments, teams, submissions, registrations or audit."""
    export = exports.EXPORTS.get(kind)
    if export is None:
        raise not_found(f"Unknown export. Choose one of: {', '.join(sorted(exports.EXPORTS))}.")
    event = get_event(event_id)
    response = HttpResponse(export(event), content_type="text/csv; charset=utf-8")
    response["Content-Disposition"] = f'attachment; filename="{event.slug}-{kind}.csv"'
    return response


# --- Assignment (organizers) ----------------------------------------------------


class AssignmentOut(Schema):
    id: str
    judge_id: str
    project_id: str
    source: str
    status: str
    reason: str


class BatchIn(Schema):
    judge_id: str
    project_ids: list[str]


class PlanOut(Schema):
    proposed: list[AssignmentOut]
    shortfalls: dict[str, int]


class PublishedOut(Schema):
    published: int


class ConflictIn(Schema):
    judge_id: str
    team_id: str
    reason: str = ""


class ConflictOut(Schema):
    judge_id: str
    team_id: str
    reason: str


def assignment_out(item: Any) -> AssignmentOut:
    return AssignmentOut(
        id=item.pk,
        judge_id=item.judge_id,
        project_id=item.project_id,
        source=item.source,
        status=item.status,
        reason=item.reason,
    )


@router.get("/events/{event_id}/assignments", response=list[AssignmentOut])
@policy(event_policies.EVENTS_MANAGE)
def list_assignments(request: HttpRequest, event_id: str, status: str = "") -> list[AssignmentOut]:
    items = get_event(event_id).assignments.all().order_by("judge_id", "project_id")
    if status:
        items = items.filter(status=status)
    return [assignment_out(item) for item in items]


@router.post("/events/{event_id}/assignments", response={201: list[AssignmentOut]})
@api_action("assignment.batch")
@policy(event_policies.EVENTS_MANAGE)
def assign(request: HttpRequest, event_id: str, payload: BatchIn) -> Status[list[AssignmentOut]]:
    items = services.assign_batch(
        get_principal(request), get_event(event_id), payload.judge_id, payload.project_ids
    )
    return Status(201, [assignment_out(item) for item in items])


@router.post("/events/{event_id}/assignments/plan", response=PlanOut)
@api_action("assignment.plan")
@policy(event_policies.EVENTS_MANAGE)
def plan(request: HttpRequest, event_id: str) -> PlanOut:
    result = services.propose_assignments(get_principal(request), get_event(event_id))
    return PlanOut(
        proposed=[assignment_out(item) for item in result.proposed], shortfalls=result.shortfalls
    )


@router.post("/events/{event_id}/assignments/publish", response=PublishedOut)
@api_action("assignment.publish")
@policy(event_policies.EVENTS_MANAGE)
def publish(request: HttpRequest, event_id: str) -> PublishedOut:
    return PublishedOut(
        published=services.publish_proposals(get_principal(request), get_event(event_id))
    )


@router.delete("/events/{event_id}/assignments/{assignment_id}", response=AssignmentOut)
@api_action("assignment.withdraw")
@policy(event_policies.EVENTS_MANAGE)
def withdraw(request: HttpRequest, event_id: str, assignment_id: str) -> AssignmentOut:
    item = services.withdraw_assignment(get_principal(request), get_event(event_id), assignment_id)
    return assignment_out(item)


@router.post("/events/{event_id}/conflicts", response={201: ConflictOut})
@api_action("judging.declare_conflict")
@policy(event_policies.EVENTS_MANAGE)
def add_conflict(request: HttpRequest, event_id: str, payload: ConflictIn) -> Status[ConflictOut]:
    record = services.declare_conflict(
        get_principal(request),
        get_event(event_id),
        payload.judge_id,
        payload.team_id,
        payload.reason,
    )
    return Status(
        201, ConflictOut(judge_id=record.judge_id, team_id=record.team_id, reason=record.reason)
    )


class JudgeProgressOut(Schema):
    judge_id: str
    email: str
    name: str
    assigned: int
    submitted: int
    drafts: int
    state: str
    last_activity: datetime | None


class CoverageOut(Schema):
    project_id: str
    project: str
    reviews: int
    needed: int


class ProgressOut(Schema):
    judges: list[JudgeProgressOut]
    coverage: list[CoverageOut]
    totals: dict[str, int]


@router.get("/events/{event_id}/progress", response=ProgressOut)
@policy(event_policies.EVENTS_MANAGE)
def event_progress(request: HttpRequest, event_id: str) -> ProgressOut:
    return ProgressOut(**services.progress(get_event(event_id)))


# --- Reviewing (judges) -------------------------------------------------------


class QueueItemOut(Schema):
    assignment_id: str
    event_id: str
    project_id: str
    project: str
    track: str | None
    team: str | None
    status: str


class CriterionOut(Schema):
    key: str
    label: str
    min_score: int
    max_score: int
    is_gate: bool
    is_bonus: bool
    anchors: dict[str, str]


class AssignmentDetailOut(Schema):
    assignment_id: str
    event_id: str
    project_id: str
    version: int
    name: str
    tagline: str
    description: str
    repo_url: str
    video_url: str
    live_url: str
    team: str | None
    criteria: list[CriterionOut]
    scores: dict[str, int]
    comment: str
    improvement: str
    review_status: str | None


class ReviewIn(Schema):
    scores: dict[str, int] = {}
    comment: str = ""
    improvement: str = ""
    submit: bool = False
    active_seconds: int = 0


def _team_label(item: Any) -> str | None:
    return None if item.event.blind_judging else item.project.team.name


@router.get("/judge/queue", response=list[QueueItemOut])
@policy(JUDGE_SELF)
def my_queue(request: HttpRequest, event: str = "") -> list[QueueItemOut]:
    return [
        QueueItemOut(
            assignment_id=item.pk,
            event_id=item.event_id,
            project_id=item.project_id,
            project=item.project.canonical_version.name if item.project.canonical_version else "",
            track=item.project.track.name if item.project.track else None,
            team=_team_label(item),
            status=item.status,
        )
        for item in services.queue(get_principal(request), event or None)
    ]


def assignment_detail(item: Any) -> AssignmentDetailOut:
    version = item.project.canonical_version
    review = services.current_review(item)
    scores = (
        {s.criterion.key: s.value for s in review.scores.select_related("criterion")}
        if review
        else {}
    )
    return AssignmentDetailOut(
        assignment_id=item.pk,
        event_id=item.event_id,
        project_id=item.project_id,
        version=version.n,
        name=version.name,
        tagline=version.tagline,
        description=version.description,
        repo_url=version.repo_url,
        video_url=version.video_url,
        live_url=version.live_url,
        team=_team_label(item),
        criteria=[
            CriterionOut(
                key=c.key,
                label=c.label,
                min_score=c.min_score,
                max_score=c.max_score,
                is_gate=c.is_gate,
                is_bonus=c.is_bonus,
                anchors=c.anchors,
            )
            for c in item.event.criteria.all()
        ],
        scores=scores,
        comment=review.comment if review else "",
        improvement=review.improvement if review else "",
        review_status=review.status if review else None,
    )


@router.get("/judge/assignments/{assignment_id}", response=AssignmentDetailOut)
@policy(JUDGE_SELF)
def my_assignment(request: HttpRequest, assignment_id: str) -> AssignmentDetailOut:
    return assignment_detail(services.own_assignment(get_principal(request), assignment_id))


@router.put("/judge/assignments/{assignment_id}/review", response=AssignmentDetailOut)
@api_action("review.save")
@policy(JUDGE_SELF)
def save_review(request: HttpRequest, assignment_id: str, payload: ReviewIn) -> AssignmentDetailOut:
    principal = get_principal(request)
    item = services.own_assignment(principal, assignment_id)
    services.save_review(
        principal,
        item,
        payload.scores,
        payload.comment,
        payload.improvement,
        submit=payload.submit,
        active_seconds=payload.active_seconds,
    )
    return assignment_detail(services.own_assignment(principal, assignment_id))


# --- Results (organizers only: judges never see aggregates) -----------------------


class ProjectResultOut(Schema):
    rank: int
    project_id: str
    project: str
    team: str
    reviews: int
    informative_reviews: int
    raw: float
    raw_rank: int
    shrunk_z: float
    additive: float
    score: float
    movement: int
    ci_low: float
    ci_high: float
    p_top: dict[int, float]
    bonus: float
    flags: list[str]


class JudgeDiagnosticsOut(Schema):
    judge: str
    n: int
    mean: float
    spread: float
    severity: float
    flat: bool
    low_n: bool
    misfit: float


class ResultsOut(Schema):
    event_id: str
    method: str
    params: dict[str, float]
    flags: list[str]
    projects: list[ProjectResultOut]
    gated_out: list[str]
    judges: list[JudgeDiagnosticsOut]


@router.get("/events/{event_id}/results", response=ResultsOut)
@policy(event_policies.EVENTS_MANAGE)
def event_results(request: HttpRequest, event_id: str, method: str = "additive") -> ResultsOut:
    """Normalized results with uncertainty. Organizers and admins only."""
    computed = results_service.compute(get_event(event_id), method=method)
    return ResultsOut(
        event_id=event_id,
        method=computed.method,
        params=computed.evaluation.params,
        flags=computed.evaluation.flags,
        projects=[ProjectResultOut(**row) for row in results_service.as_rows(computed)],
        gated_out=computed.gated_out,
        judges=[JudgeDiagnosticsOut(**j.__dict__) for j in computed.evaluation.judges],
    )


# --- Measure, Doubt, Ask -----------------------------------------------------------


class CloseCallOut(Schema):
    project_id: str
    cutoff: int
    chance: float


class ProposalOut(Schema):
    judge_id: str
    project_id: str
    reason: str


class CloseCallsOut(Schema):
    event_id: str
    close_calls: list[CloseCallOut]
    waiting: dict[str, list[str]]
    proposals: list[ProposalOut]
    shortfalls: dict[str, int]
    budget: int


class PairIn(Schema):
    judge_id: str
    project_id: str


class AskIn(Schema):
    budget: int = Field(close_calls.DEFAULT_BUDGET, ge=1, le=100)
    pairs: list[PairIn] | None = None


@router.get("/events/{event_id}/close-calls", response=CloseCallsOut)
@policy(event_policies.EVENTS_MANAGE)
def list_close_calls(
    request: HttpRequest, event_id: str, budget: int = close_calls.DEFAULT_BUDGET
) -> CloseCallsOut:
    """Close calls on the prize places, and the extra reviews proposed to settle them."""
    budget = min(max(budget, 1), 100)
    found = close_calls.report(get_event(event_id), budget=budget)
    return CloseCallsOut(
        event_id=event_id,
        close_calls=[
            CloseCallOut(project_id=c.project.id, cutoff=c.cutoff, chance=c.p)
            for c in found.close_calls
        ],
        waiting=found.waiting,
        proposals=[
            ProposalOut(judge_id=p.judge, project_id=p.project, reason=p.reason)
            for p in found.proposals
        ],
        shortfalls=found.shortfalls,
        budget=found.budget,
    )


@router.post("/events/{event_id}/close-calls/ask", response={201: list[AssignmentOut]})
@api_action("judging.close_calls_ask")
@policy(event_policies.EVENTS_MANAGE)
def ask_close_calls(
    request: HttpRequest, event_id: str, payload: AskIn
) -> Status[list[AssignmentOut]]:
    """Approve the proposals (all, or the listed pairs): judges are assigned and emailed."""
    pairs = [(p.judge_id, p.project_id) for p in payload.pairs] if payload.pairs else None
    created = close_calls.approve(
        get_principal(request), get_event(event_id), budget=payload.budget, pairs=pairs
    )
    return Status(201, [assignment_out(item) for item in created])


# --- Deliberation ----------------------------------------------------------------


class BoardRowOut(Schema):
    final_rank: int
    computed_rank: int
    project_id: str
    name: str
    team: str
    score: float
    bonus: float
    ci_low: float
    ci_high: float
    p_top: dict[int, float]
    flags: list[str]
    notes: list[str]
    decided: bool


class DecisionOut(Schema):
    id: str
    kind: str
    project_id: str
    other_id: str | None
    rationale: str
    actor_id: str
    created_at: datetime


class BoardOut(Schema):
    event_id: str
    prize_places: int
    rows: list[BoardRowOut]
    undecided: list[str]
    decisions: list[DecisionOut]


class DecisionIn(Schema):
    kind: str = Field(..., description="confirm or place_above")
    project_id: str
    other_id: str | None = None
    rationale: str


def decision_out(item: Any) -> DecisionOut:
    return DecisionOut(
        id=item.pk,
        kind=item.kind,
        project_id=item.project_id,
        other_id=item.other_id,
        rationale=item.rationale,
        actor_id=item.actor_id,
        created_at=item.created_at,
    )


@router.get("/events/{event_id}/deliberation", response=BoardOut)
@policy(event_policies.EVENTS_MANAGE)
def deliberation_board(request: HttpRequest, event_id: str) -> BoardOut:
    """The computed ranking with decisions applied, open close calls and the decision log."""
    found = deliberation.board(get_event(event_id))
    return BoardOut(
        event_id=event_id,
        prize_places=found.prize_places,
        rows=[BoardRowOut(**{**row.__dict__}) for row in found.rows],
        undecided=[row.project_id for row in found.undecided],
        decisions=[decision_out(d) for d in found.decisions],
    )


@router.post("/events/{event_id}/deliberation/decisions", response={201: DecisionOut})
@api_action("judging.record_decision")
@policy(event_policies.EVENTS_MANAGE)
def add_decision(request: HttpRequest, event_id: str, payload: DecisionIn) -> Status[DecisionOut]:
    decision = deliberation.record_decision(
        get_principal(request),
        get_event(event_id),
        kind=payload.kind,
        project_id=payload.project_id,
        other_id=payload.other_id,
        rationale=payload.rationale,
    )
    return Status(201, decision_out(decision))


# --- Signed results snapshots --------------------------------------------------------


class SnapshotOut(Schema):
    number: int
    created_at: datetime
    method: str
    payload_hash: str
    input_hash: str
    key_id: str
    decisions: int


def snapshot_out(item: Any) -> SnapshotOut:
    return SnapshotOut(
        number=item.number,
        created_at=item.created_at,
        method=item.method,
        payload_hash=item.payload_hash,
        input_hash=item.input_hash,
        key_id=item.key_id,
        decisions=len(item.payload.get("decisions", [])),
    )


@router.get("/events/{event_id}/results/snapshots", response=list[SnapshotOut])
@policy(event_policies.EVENTS_MANAGE)
def list_snapshots(request: HttpRequest, event_id: str) -> list[SnapshotOut]:
    return [snapshot_out(s) for s in get_event(event_id).results_snapshots.order_by("number")]


@router.post("/events/{event_id}/results/snapshots", response={201: SnapshotOut})
@api_action("judging.freeze_results")
@policy(event_policies.EVENTS_MANAGE)
def freeze_results(request: HttpRequest, event_id: str) -> Status[SnapshotOut]:
    """Freeze the current final order into a signed, immutable snapshot."""
    return Status(201, snapshot_out(snapshots.freeze(get_principal(request), get_event(event_id))))


@router.get("/events/{event_id}/results/snapshots/{number}")
@policy(event_policies.EVENTS_MANAGE)
def get_snapshot(request: HttpRequest, event_id: str, number: int) -> dict[str, Any]:
    """The signed document: payload, its SHA-256 and the Ed25519 signature."""
    item = get_event(event_id).results_snapshots.filter(number=number).first()
    if item is None:
        raise not_found("No such snapshot.")
    return snapshots.signed_document(item)


PUBLIC_RESULTS_API = define(
    "public.results_document",
    Rule(public=True, description="The signed results document, once published."),
)


@router.get("/events/{event_id}/results/published")
@policy(PUBLIC_RESULTS_API)
def published_results(request: HttpRequest, event_id: str) -> dict[str, Any]:
    """The published, signed results. Verify it with the key at /.well-known/raptor-desk-key."""
    publication = publishing.published(get_event(event_id))
    if publication is None:
        raise not_found("Results for this event have not been published.")
    return {
        **snapshots.signed_document(publication.snapshot),
        "published_at": publication.published_at.isoformat(),
    }
