import json
import math
from pathlib import Path

import numpy as np
import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from scoring_engine import simulate
from scoring_engine.normalization import (
    Observation,
    additive,
    evaluate,
    flat_judges,
    naive_z,
    raw_means,
    shrunk_z,
)

FIXTURES = Path(__file__).resolve().parents[2] / "data" / "fixtures.json"


def fixture_observations() -> list[Observation]:
    """Latest review per judge (prj_41 is version 2 of prj_07), equal-weight composite."""
    data = json.loads(FIXTURES.read_text(encoding="utf-8"))
    latest = {}
    for score in data["scores"]:
        project = "prj_07" if score["project"] == "prj_41" else score["project"]
        c = score["criteria"]
        vector = (c["functionality"], c["quality"], c["innovation"])
        latest[(score["judge"], project)] = (sum(vector) / 3, vector)
    return [Observation(j, p, v, vec) for (j, p), (v, vec) in latest.items()]


# --- Golden results on the official fixtures -----------------------------------------


@pytest.fixture(scope="module")
def fixture_eval():
    return evaluate(fixture_observations(), bootstrap=300, seed=0)


def test_only_the_rubber_stamping_judge_is_flat() -> None:
    assert flat_judges(fixture_observations()) == {"jdg_07"}


def test_naive_z_score_breaks_on_the_fixtures() -> None:
    broken = sorted(p for p, v in naive_z(fixture_observations()).items() if math.isnan(v))
    assert broken == ["prj_03", "prj_07", "prj_08", "prj_09", "prj_17", "prj_19", "prj_24"]


def test_our_estimators_never_produce_nan_on_the_fixtures(fixture_eval) -> None:
    for project in fixture_eval.projects:
        assert all(not math.isnan(v) for v in project.scores.values())


def test_raw_tie_for_first_is_resolved(fixture_eval) -> None:
    raw = raw_means(fixture_observations())
    assert raw["prj_11"] == pytest.approx(raw["prj_34"])
    assert fixture_eval.ranking("additive")[0] == "prj_34"
    assert fixture_eval.ranking("shrunk_z")[0] == "prj_34"


def test_lenient_judge_luck_is_removed(fixture_eval) -> None:
    ranks = fixture_eval.by_project()["prj_10"].ranks
    assert ranks["raw"] == 3
    assert ranks["additive"] > 5 and ranks["shrunk_z"] > 5


def test_additive_top_five(fixture_eval) -> None:
    assert fixture_eval.ranking("additive")[:5] == [
        "prj_34",
        "prj_11",
        "prj_37",
        "prj_25",
        "prj_33",
    ]


def test_places_three_to_five_are_flagged_as_close_calls(fixture_eval) -> None:
    by = fixture_eval.by_project()
    assert by["prj_34"].p_top[5] > 0.8 and by["prj_11"].p_top[5] > 0.8
    for project in ("prj_37", "prj_25", "prj_33"):
        assert "close_call_top5" in by[project].flags
    assert "method_disagreement_top3" in fixture_eval.flags


def test_weak_evidence_is_flagged(fixture_eval) -> None:
    assert "weak_evidence" in fixture_eval.by_project()["prj_19"].flags


def test_judge_severity_is_measured(fixture_eval) -> None:
    by = {j.judge: j for j in fixture_eval.judges}
    assert by["jdg_02"].severity > 0.4  # the most lenient
    assert by["jdg_01"].severity < -0.5  # harsh, and only one review
    assert by["jdg_01"].low_n
    assert by["jdg_07"].flat


def test_results_are_reproducible(fixture_eval) -> None:
    again = evaluate(fixture_observations(), bootstrap=300, seed=0)
    assert [p.p_top for p in again.projects] == [p.p_top for p in fixture_eval.projects]


# --- Proof: recovery of a known truth ------------------------------------------------


def test_bias_correction_recovers_the_true_order_better_than_averaging() -> None:
    raw_rho, add_rho, z_rho, raw_top, add_top = [], [], [], [], []
    for seed in range(40):
        event = simulate.generate(seed)
        raw = raw_means(event.observations)
        fit = [o for o in event.observations if o.judge not in flat_judges(event.observations)]
        quality = additive(fit)[0]
        for project, value in raw.items():
            quality.setdefault(project, value)
        zs = shrunk_z(fit)
        raw_rho.append(simulate.spearman(raw, event.truth))
        add_rho.append(simulate.spearman(quality, event.truth))
        z_rho.append(simulate.spearman({**raw, **zs}, event.truth))
        raw_top.append(simulate.top_k_recall(raw, event.truth))
        add_top.append(simulate.top_k_recall(quality, event.truth))
    assert np.mean(add_rho) > np.mean(raw_rho) + 0.02
    assert np.mean(z_rho) > np.mean(raw_rho)
    assert np.mean(add_top) >= np.mean(raw_top)


# --- Properties -----------------------------------------------------------------


observations = st.lists(
    st.builds(
        Observation,
        judge=st.sampled_from(["a", "b", "c", "d"]),
        project=st.sampled_from(["p1", "p2", "p3", "p4", "p5"]),
        score=st.integers(1, 5).map(float),
    ),
    min_size=1,
    max_size=30,
)


@settings(max_examples=80, deadline=None)
@given(observations)
def test_no_input_produces_nan_or_infinity(obs: list[Observation]) -> None:
    result = evaluate(obs, bootstrap=5, seed=1)
    for project in result.projects:
        assert all(math.isfinite(v) for v in project.scores.values())
        assert math.isfinite(project.ci_low) and math.isfinite(project.ci_high)
        assert all(0.0 <= p <= 1.0 for p in project.p_top.values())


def test_single_judge_single_review() -> None:
    result = evaluate([Observation("a", "p", 3.0)], bootstrap=5)
    assert result.projects[0].scores["additive"] == pytest.approx(3.0)


def test_empty_input() -> None:
    assert evaluate([]).projects == []
