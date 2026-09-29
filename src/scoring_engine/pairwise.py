"""Pairwise mode: a Bradley-Terry estimator for "which of these two is stronger?".

Judges who struggle to hold a 1 to 5 scale steady can still say reliably which of
two projects is better. The Bradley-Terry model turns such comparisons into a
strength per project:

    P(i beats j) = s_i / (s_i + s_j)

It is fitted with Hunter's MM algorithm (2004), which increases the likelihood
at every step. A tie counts as half a win for each side. A weak prior (one
virtual win and one virtual loss against an average reference project) keeps
every strength finite, including a project that never lost, and pulls
thinly-compared projects towards the middle instead of the extremes.

Uncertainty comes from resampling the comparisons (a seeded bootstrap), in the
same way as the score-based ranking: a 90% interval on each log-strength and
each project's chance of finishing inside every cutoff.
"""

import math
import random
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass, field

A_WINS, B_WINS, TIE = "a", "b", "tie"


@dataclass(frozen=True)
class Comparison:
    a: str
    b: str
    outcome: str  # "a", "b" or "tie"
    judge: str = ""


@dataclass
class Standing:
    project: str
    strength: float  # log-strength; 0 is the reference (an average project)
    rank: int
    comparisons: int
    wins: float
    ci_low: float = 0.0
    ci_high: float = 0.0
    p_top: dict[int, float] = field(default_factory=dict)


def _tally(
    comparisons: Iterable[Comparison],
) -> tuple[dict[str, float], dict[tuple[str, str], int]]:
    wins: dict[str, float] = defaultdict(float)
    games: dict[tuple[str, str], int] = defaultdict(int)
    for c in comparisons:
        if c.a == c.b:
            continue
        pair = (c.a, c.b) if c.a < c.b else (c.b, c.a)
        games[pair] += 1
        if c.outcome == A_WINS:
            wins[c.a] += 1
        elif c.outcome == B_WINS:
            wins[c.b] += 1
        else:
            wins[c.a] += 0.5
            wins[c.b] += 0.5
    return wins, games


def fit(
    comparisons: Iterable[Comparison],
    items: Iterable[str] | None = None,
    *,
    prior: float = 1.0,
    iterations: int = 500,
    tolerance: float = 1e-10,
) -> dict[str, float]:
    """Log-strengths per project. Projects with no comparisons get 0 (the reference)."""
    comparisons = list(comparisons)
    names = set(items or [])
    for c in comparisons:
        names.update((c.a, c.b))
    if not names:
        return {}
    wins, games = _tally(comparisons)
    opponents: dict[str, list[tuple[str, int]]] = defaultdict(list)
    for (i, j), n in games.items():
        opponents[i].append((j, n))
        opponents[j].append((i, n))

    strength = dict.fromkeys(names, 1.0)
    for _ in range(iterations):
        biggest_change = 0.0
        for i in sorted(names):
            # Real games, plus 2 * prior virtual games against a reference of strength 1.
            denominator = sum(n / (strength[i] + strength[j]) for j, n in opponents[i])
            denominator += 2 * prior / (strength[i] + 1.0)
            numerator = wins[i] + prior
            updated = numerator / denominator
            biggest_change = max(biggest_change, abs(math.log(updated / strength[i])))
            strength[i] = updated
        if biggest_change < tolerance:
            break
    return {name: math.log(value) for name, value in strength.items()}


def _ranks(scores: dict[str, float]) -> dict[str, int]:
    ordered = sorted(scores, key=lambda k: (-round(scores[k], 12), k))
    return {name: position for position, name in enumerate(ordered, start=1)}


def standings(
    comparisons: Iterable[Comparison],
    items: Iterable[str] | None = None,
    *,
    cutoffs: tuple[int, ...] = (1, 2, 3, 5),
    bootstrap: int = 200,
    seed: int = 0,
    prior: float = 1.0,
) -> list[Standing]:
    comparisons = sorted(comparisons, key=lambda c: (c.a, c.b, c.outcome, c.judge))
    names = set(items or [])
    for c in comparisons:
        names.update((c.a, c.b))
    strengths = fit(comparisons, names, prior=prior)
    ranks = _ranks(strengths)
    wins, _ = _tally(comparisons)
    played: dict[str, int] = defaultdict(int)
    for c in comparisons:
        played[c.a] += 1
        played[c.b] += 1

    samples: dict[str, list[float]] = defaultdict(list)
    top: dict[str, dict[int, int]] = {n: dict.fromkeys(cutoffs, 0) for n in names}
    rng = random.Random(seed)  # noqa: S311 (resampling, not security)
    for _ in range(bootstrap if comparisons else 0):
        resampled = [comparisons[rng.randrange(len(comparisons))] for _ in comparisons]
        estimate = fit(resampled, names, prior=prior, iterations=200, tolerance=1e-8)
        sample_ranks = _ranks(estimate)
        for name in names:
            samples[name].append(estimate[name])
            for cutoff in cutoffs:
                if sample_ranks[name] <= cutoff:
                    top[name][cutoff] += 1

    result = []
    for name in names:
        values = sorted(samples[name]) or [strengths[name]]
        low = values[int(0.05 * (len(values) - 1))]
        high = values[round(0.95 * (len(values) - 1))]
        runs = max(len(samples[name]), 1)
        result.append(
            Standing(
                project=name,
                strength=strengths[name],
                rank=ranks[name],
                comparisons=played[name],
                wins=wins[name],
                ci_low=low,
                ci_high=high,
                p_top={c: top[name][c] / runs for c in cutoffs} if samples[name] else {},
            )
        )
    return sorted(result, key=lambda s: s.rank)


def win_probability(strength_a: float, strength_b: float) -> float:
    """P(a beats b) from two log-strengths."""
    return 1.0 / (1.0 + math.exp(strength_b - strength_a))


def next_pair(
    candidates: list[str],
    done: set[frozenset[str]],
    strengths: dict[str, float],
    priority: set[str] | None = None,
) -> tuple[str, str] | None:
    """The most informative pair this judge has not compared yet.

    Informative means the outcome is least predictable (strengths closest), with
    pairs involving a close-call project first. Ties break on names, so the
    choice is reproducible.
    """
    priority = priority or set()
    best: tuple[tuple[int, float, str, str], tuple[str, str]] | None = None
    ordered = sorted(set(candidates))
    for i, a in enumerate(ordered):
        for b in ordered[i + 1 :]:
            if frozenset((a, b)) in done:
                continue
            gap = abs(strengths.get(a, 0.0) - strengths.get(b, 0.0))
            key = (-len({a, b} & priority), gap, a, b)
            if best is None or key < best[0]:
                best = (key, (a, b))
    return best[1] if best else None
