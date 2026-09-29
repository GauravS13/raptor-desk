"""Calibration: does the engine's stated uncertainty match what really happens?

Over many synthetic events with a known truth, two questions:

- Interval coverage: how often does a project's 90% interval contain its true
  quality? The model fixes quality only up to a shift (every score +c, every
  judge's leniency -c fits equally well), so each event's estimates are moved
  to the truth's mean before comparing.
- P(top k): among projects given a chance near p of finishing in the top k,
  what share really are in the true top k? Scored with the Brier score, next to
  the score of always saying the base rate.
- Doubt: of the projects the ranking puts on the wrong side of the top-k line,
  what share did the engine flag as a close call beforehand?
"""

from dataclasses import dataclass
from itertools import pairwise

import numpy as np

from scoring_engine import simulate
from scoring_engine.normalization import evaluate

BINS = (0.0, 0.05, 0.2, 0.5, 0.8, 0.95, 1.0)


@dataclass(frozen=True)
class Bin:
    low: float
    high: float
    count: int
    stated: float
    observed: float


@dataclass(frozen=True)
class Calibration:
    runs: int
    projects: int
    coverage: float
    coverage_by_reviews: dict[str, float]
    bins: list[Bin]
    brier: float
    brier_base_rate: float
    line_errors: int
    line_errors_flagged: int


def calibrate(runs: int = 100, *, cutoff: int = 5, bootstrap: int = 300) -> Calibration:
    inside: list[bool] = []
    by_reviews: dict[str, list[bool]] = {"1": [], "2": [], "3+": []}
    stated: list[float] = []
    happened: list[bool] = []
    errors = flagged = 0
    for seed in range(runs):
        event = simulate.generate(seed)
        result = evaluate(event.observations, bootstrap=bootstrap, seed=seed, cutoffs=(cutoff,))
        truth = event.truth
        ranked = [p for p in result.projects if p.project in truth]
        shift = np.mean([truth[p.project] for p in ranked]) - np.mean(
            [p.scores["additive"] for p in ranked]
        )
        true_top = set(sorted(truth, key=lambda p: -truth[p])[:cutoff])
        for p in ranked:
            hit = p.ci_low + shift <= truth[p.project] <= p.ci_high + shift
            inside.append(hit)
            n = p.informative_reviews
            by_reviews["1" if n <= 1 else "2" if n == 2 else "3+"].append(hit)
            stated.append(p.p_top[cutoff])
            happened.append(p.project in true_top)
            if (p.ranks["additive"] <= cutoff) != (p.project in true_top):
                errors += 1
                flagged += f"close_call_top{cutoff}" in p.flags
    stated_arr, happened_arr = np.array(stated), np.array(happened, dtype=float)
    bins = []
    for low, high in pairwise(BINS):
        mask = (stated_arr >= low) & ((stated_arr < high) | (high == 1.0))
        if mask.any():
            bins.append(
                Bin(
                    low,
                    high,
                    int(mask.sum()),
                    float(stated_arr[mask].mean()),
                    float(happened_arr[mask].mean()),
                )
            )
    base = float(happened_arr.mean())
    return Calibration(
        runs=runs,
        projects=len(inside),
        coverage=float(np.mean(inside)),
        coverage_by_reviews={k: float(np.mean(v)) for k, v in by_reviews.items() if v},
        bins=bins,
        brier=float(np.mean((stated_arr - happened_arr) ** 2)),
        brier_base_rate=float(np.mean((base - happened_arr) ** 2)),
        line_errors=errors,
        line_errors_flagged=flagged,
    )
