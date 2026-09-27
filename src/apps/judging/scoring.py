"""Adapters between the database and the pure scoring engine."""

from dataclasses import dataclass

from apps.events.models import Event
from apps.judging.models import Review
from scoring_engine import weighting


def engine_criteria(event: Event) -> list[weighting.Criterion]:
    return [
        weighting.Criterion(
            key=c.key,
            weight=float(c.weight),
            min_score=float(c.min_score),
            max_score=float(c.max_score),
            is_gate=c.is_gate,
            gate_threshold=float(c.gate_threshold) if c.gate_threshold is not None else None,
            is_bonus=c.is_bonus,
        )
        for c in event.criteria.all()
    ]


def score_map(review: Review) -> dict[str, float]:
    return {item.criterion.key: float(item.value) for item in review.scores.all()}


@dataclass(frozen=True)
class ReviewScore:
    review_id: str
    judge_id: str
    project_id: str
    version_n: int
    composite: float | None


def latest_reviews(reviews: list[Review]) -> list[Review]:
    """Each judge's submitted review of the most recent version they reviewed, per project."""
    latest: dict[tuple[str, str], Review] = {}
    for review in reviews:
        if review.status != "submitted":
            continue
        key = (review.judge_id, review.project_id)
        if key not in latest or review.version.n > latest[key].version.n:
            latest[key] = review
    return list(latest.values())


def review_scores(event: Event, reviews: list[Review]) -> list[ReviewScore]:
    criteria = engine_criteria(event)
    return [
        ReviewScore(
            review_id=r.pk,
            judge_id=r.judge_id,
            project_id=r.project_id,
            version_n=r.version.n,
            composite=weighting.composite(score_map(r), criteria),
        )
        for r in latest_reviews(reviews)
    ]
