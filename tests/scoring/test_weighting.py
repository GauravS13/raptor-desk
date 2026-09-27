import math

from hypothesis import given
from hypothesis import strategies as st

from scoring_engine.weighting import Criterion, bonus_points, composite, passes_gates

DOGFOOD = [
    Criterion("t1_cleared", 0, 0, 1, is_gate=True, gate_threshold=1),
    Criterion("tier_completion", 40, 0, 5),
    Criterion("judging_integrity", 25, 0, 5),
    Criterion("adoptability", 20, 0, 5),
    Criterion("code_quality", 15, 0, 5),
    Criterion("normalization_proof", 5, 0, 1, is_bonus=True),
    Criterion("threat_model", 3, 0, 1, is_bonus=True),
]


def test_weighted_mean_ignores_gate_and_bonus() -> None:
    scores = {
        "t1_cleared": 1,
        "tier_completion": 5,
        "judging_integrity": 4,
        "adoptability": 3,
        "code_quality": 2,
        "normalization_proof": 1,
        "threat_model": 0,
    }
    expected = (40 * 5 + 25 * 4 + 20 * 3 + 15 * 2) / 100
    assert math.isclose(composite(scores, DOGFOOD), expected)


def test_multiplier_weights_work_the_same_way() -> None:
    criteria = [Criterion("tech", 1.4), Criterion("docs", 1.0)]
    assert math.isclose(composite({"tech": 5, "docs": 3}, criteria), (1.4 * 5 + 3) / 2.4)


def test_missing_criterion_is_left_out_not_zero() -> None:
    criteria = [Criterion("a", 1), Criterion("b", 1)]
    assert composite({"a": 4}, criteria) == 4
    assert composite({}, criteria) is None


def test_gate_below_threshold_fails() -> None:
    assert passes_gates({"t1_cleared": 1}, DOGFOOD)
    assert not passes_gates({"t1_cleared": 0}, DOGFOOD)
    assert passes_gates({}, DOGFOOD)  # an unanswered gate does not fail the project by itself


def test_bonus_points_only_count_achieved_bonuses() -> None:
    assert bonus_points({"normalization_proof": 1, "threat_model": 0}, DOGFOOD) == 5


@given(st.dictionaries(st.sampled_from(["a", "b", "c"]), st.integers(1, 5), min_size=1))
def test_composite_stays_inside_the_scale(scores: dict[str, int]) -> None:
    criteria = [Criterion("a", 3), Criterion("b", 1), Criterion("c", 0.5)]
    value = composite(scores, criteria)
    assert value is not None
    assert min(scores.values()) <= value <= max(scores.values())
