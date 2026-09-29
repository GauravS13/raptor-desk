import math
import random

from scoring_engine.pairwise import (
    A_WINS,
    B_WINS,
    TIE,
    Comparison,
    fit,
    next_pair,
    standings,
    win_probability,
)
from scoring_engine.simulate import spearman


def _simulate(n_projects: int, n_comparisons: int, seed: int) -> tuple[dict, list[Comparison]]:
    rng = random.Random(seed)
    truth = {f"p{i:02d}": rng.gauss(0, 1) for i in range(n_projects)}
    names = sorted(truth)
    comparisons = []
    for _ in range(n_comparisons):
        a, b = rng.sample(names, 2)
        outcome = A_WINS if rng.random() < win_probability(truth[a], truth[b]) else B_WINS
        comparisons.append(Comparison(a, b, outcome))
    return truth, comparisons


def test_recovers_a_known_order() -> None:
    truth, comparisons = _simulate(20, 600, seed=1)
    estimate = fit(comparisons)
    assert spearman(estimate, truth) > 0.85


def test_mm_is_consistent_with_the_model() -> None:
    # A beats B twice, B beats A once: the maximum-likelihood odds are 2:1,
    # shrunk slightly by the prior.
    comparisons = [Comparison("a", "b", A_WINS)] * 2 + [Comparison("a", "b", B_WINS)]
    estimate = fit(comparisons, prior=1e-6)
    assert math.isclose(win_probability(estimate["a"], estimate["b"]), 2 / 3, rel_tol=1e-3)


def test_unbeaten_and_uncompared_projects_stay_finite() -> None:
    comparisons = [Comparison("a", "b", A_WINS), Comparison("a", "c", A_WINS)]
    estimate = fit(comparisons, items=["a", "b", "c", "lonely"])
    assert all(math.isfinite(v) for v in estimate.values())
    assert estimate["lonely"] == 0.0
    assert estimate["a"] > 0 > estimate["b"]


def test_a_tie_is_half_a_win_each() -> None:
    estimate = fit([Comparison("a", "b", TIE)] * 5)
    assert math.isclose(estimate["a"], estimate["b"], abs_tol=1e-9)


def test_standings_are_reproducible_and_carry_uncertainty() -> None:
    _, comparisons = _simulate(8, 120, seed=3)
    first = standings(comparisons, bootstrap=100, seed=0)
    again = standings(list(reversed(comparisons)), bootstrap=100, seed=0)
    assert [(s.project, s.p_top) for s in first] == [(s.project, s.p_top) for s in again]
    assert [s.rank for s in first] == list(range(1, 9))
    assert all(s.ci_low <= s.strength <= s.ci_high for s in first)


def test_next_pair_prefers_close_calls_then_the_closest_strengths() -> None:
    strengths = {"a": 1.0, "b": 0.9, "c": -1.0, "d": 0.2}
    assert next_pair(["a", "b", "c", "d"], set(), strengths) == ("a", "b")
    assert next_pair(["a", "b", "c", "d"], {frozenset(("a", "b"))}, strengths) == ("b", "d")
    assert next_pair(["a", "b", "c", "d"], set(), strengths, priority={"c"}) == ("c", "d")
    every = {
        frozenset(p)
        for p in [("a", "b"), ("a", "c"), ("a", "d"), ("b", "c"), ("b", "d"), ("c", "d")]
    }
    assert next_pair(["a", "b", "c", "d"], every, strengths) is None
