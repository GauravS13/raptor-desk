"""Data hygiene findings for an event: the awkward cases a judging platform must survive.

Computed from the database (not from the fixture file), so the same report
works for any event: flat-lining judges, judges with too few reviews to
calibrate, projects short of reviews, missing written feedback, resubmissions
and thin tracks.
"""

from collections import Counter, defaultdict
from dataclasses import dataclass

from apps.events.models import Event
from apps.judging.models import JudgeTrack, Review


@dataclass(frozen=True)
class Finding:
    code: str
    message: str


def _latest_reviews(event: Event) -> dict[tuple[str, str], Review]:
    """Each judge's review of the most recent version of each project."""
    latest: dict[tuple[str, str], Review] = {}
    reviews = (
        Review.objects.filter(event=event, status="submitted")
        .select_related("version")
        .prefetch_related("scores")
    )
    for review in reviews:
        key = (review.judge_id, review.project_id)
        if key not in latest or review.version.n > latest[key].version.n:
            latest[key] = review
    return latest


def findings(event: Event) -> list[Finding]:
    result: list[Finding] = []
    latest = _latest_reviews(event)

    # Resubmissions merged into versions.
    multi = [p for p in event.projects.all() if p.versions.count() > 1]
    for project in multi:
        refs = ", ".join(v.source_ref or f"v{v.n}" for v in project.versions.order_by("n"))
        per_judge_reviews = Counter(
            Review.objects.filter(project=project).values_list("judge_id", flat=True)
        )
        double = sum(1 for count in per_judge_reviews.values() if count > 1)
        result.append(
            Finding(
                "resubmission",
                f"'{project.canonical_version.name}' was submitted {project.versions.count()} "
                f"times ({refs}); kept as one project with versions. {double} judge(s) scored "
                "more than one version; each judge's latest review counts.",
            )
        )

    # Judge-level patterns.
    per_judge: dict[str, list[tuple[int, ...]]] = defaultdict(list)
    for (judge_id, _), review in latest.items():
        vector = tuple(s.value for s in sorted(review.scores.all(), key=lambda s: s.criterion_id))
        per_judge[judge_id].append(vector)
    flat = sorted(
        j for j, vectors in per_judge.items() if len(vectors) >= 2 and len(set(vectors)) == 1
    )
    for judge_id in flat:
        result.append(
            Finding(
                "flat_liner",
                f"Judge {judge_id} gave identical scores to all {len(per_judge[judge_id])} "
                "projects they reviewed: their reviews carry no ranking signal.",
            )
        )
    single = sorted(j for j, vectors in per_judge.items() if len(vectors) == 1)
    if single:
        result.append(
            Finding(
                "single_review_judges",
                f"Judges with a single review cannot be calibrated: {', '.join(single)}.",
            )
        )

    # Coverage.
    per_project: dict[str, set[str]] = defaultdict(set)
    informative: dict[str, set[str]] = defaultdict(set)
    for judge_id, project_id in latest:
        per_project[project_id].add(judge_id)
        if judge_id not in flat:
            informative[project_id].add(judge_id)
    projects = {p.pk: p for p in event.projects.filter(status="submitted")}
    short = sorted(pid for pid in projects if len(per_project[pid]) < event.reviews_per_project)
    if short:
        result.append(
            Finding(
                "under_reviewed",
                f"{len(short)} project(s) have fewer than {event.reviews_per_project} reviews: "
                + ", ".join(f"{pid} ({len(per_project[pid])})" for pid in short),
            )
        )
    weak = sorted(pid for pid in projects if len(informative[pid]) < 2)
    if weak:
        result.append(
            Finding(
                "weak_evidence",
                "Projects with fewer than 2 informative reviews (flat-liner reviews excluded): "
                + ", ".join(f"{pid} ({len(informative[pid])})" for pid in weak),
            )
        )

    # Written feedback.
    empty = sum(1 for review in latest.values() if not review.comment.strip())
    if empty:
        result.append(
            Finding(
                "missing_feedback",
                f"{empty} of {len(latest)} reviews have no written comment; those teams "
                "would receive scores without feedback.",
            )
        )

    # Thin tracks.
    judges_per_track: dict[str, int] = defaultdict(int)
    for judge_track in JudgeTrack.objects.filter(event=event).select_related("track"):
        judges_per_track[judge_track.track.name] += 1
    thin = sorted(
        name for name, count in judges_per_track.items() if count <= event.reviews_per_project
    )
    if thin:
        result.append(
            Finding(
                "thin_tracks",
                "Tracks with no more eligible judges than reviews needed per project "
                f"({event.reviews_per_project}): "
                + ", ".join(f"{name} ({judges_per_track[name]})" for name in thin),
            )
        )
    return result
