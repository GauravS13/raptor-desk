"""Turn one review's criterion scores into a single composite score.

The composite is a weighted mean on the rubric's own scale, so adding a
criterion or changing a weight never stretches the range:

    composite = sum(w_c * x_c) / sum(w_c)   over scored criteria present in the review

Gate criteria decide whether a project is scored at all and bonus criteria only
break ties, so neither enters the composite. A criterion missing from a review
is left out of both sums instead of being treated as zero.
"""

from collections.abc import Mapping
from dataclasses import dataclass


@dataclass(frozen=True)
class Criterion:
    key: str
    weight: float
    min_score: float = 1.0
    max_score: float = 5.0
    is_gate: bool = False
    gate_threshold: float | None = None
    is_bonus: bool = False

    @property
    def is_scored(self) -> bool:
        return not self.is_gate and not self.is_bonus


def composite(scores: Mapping[str, float], criteria: list[Criterion]) -> float | None:
    """Weighted mean of the scored criteria present in ``scores``; None if none are present."""
    total = 0.0
    weights = 0.0
    for criterion in criteria:
        if not criterion.is_scored or criterion.key not in scores:
            continue
        total += criterion.weight * float(scores[criterion.key])
        weights += criterion.weight
    if weights == 0:
        return None
    return total / weights


def passes_gates(scores: Mapping[str, float], criteria: list[Criterion]) -> bool:
    """True unless a gate criterion is scored below its threshold."""
    for criterion in criteria:
        if not criterion.is_gate or criterion.key not in scores:
            continue
        threshold = criterion.gate_threshold if criterion.gate_threshold is not None else 0.0
        if float(scores[criterion.key]) < threshold:
            return False
    return True


def bonus_points(scores: Mapping[str, float], criteria: list[Criterion]) -> float:
    """Tie-break points: weight times the fraction of the bonus achieved."""
    points = 0.0
    for criterion in criteria:
        if not criterion.is_bonus or criterion.key not in scores:
            continue
        span = criterion.max_score - criterion.min_score
        achieved = (float(scores[criterion.key]) - criterion.min_score) / span if span else 0.0
        points += criterion.weight * max(0.0, min(1.0, achieved))
    return points
