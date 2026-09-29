from scoring_engine.influence import kingmakers
from scoring_engine.normalization import Observation


def test_a_judge_who_alone_decides_first_place_is_named() -> None:
    # Three judges put a narrowly ahead of b; a fourth rates b far higher and alone flips it.
    obs = [
        Observation(judge, project, q)
        for judge in ("j1", "j2", "j3")
        for project, q in {"a": 4.0, "b": 3.9, "c": 3.0}.items()
    ]
    obs += [Observation("j4", "a", 3.0), Observation("j4", "b", 5.0), Observation("j4", "c", 3.0)]
    found = kingmakers(obs, places=1)
    assert [f.judge for f in found] == ["j4"]
    only = found[0]
    assert only.changes_winner and only.winner_before == "b" and only.winner_after == "a"
    assert only.entered == ("a",) and only.left == ("b",) and only.reviews == 3


def test_a_consensus_panel_has_no_kingmaker() -> None:
    obs = [
        Observation(judge, project, q)
        for judge in ("j1", "j2", "j3", "j4")
        for project, q in {"a": 4.5, "b": 4.0, "c": 3.0, "d": 2.0}.items()
    ]
    assert kingmakers(obs, places=2) == []


def test_projects_left_without_evidence_are_not_blamed_on_influence() -> None:
    obs = [
        Observation("j1", "a", 4.0),
        Observation("j1", "b", 3.0),
        Observation("j2", "a", 4.0),
        Observation("j2", "b", 3.0),
        Observation("solo", "z", 5.0),  # only this judge saw z
    ]
    found = {f.judge: f for f in kingmakers(obs, places=1)}
    assert "solo" not in found
