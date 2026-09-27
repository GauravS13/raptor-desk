from scoring_engine.ask import CloseCall, most_uncertain, propose
from scoring_engine.assignment import JudgeInput, ProjectInput


def _p(pid: str, track: str | None = None, team: str | None = None) -> ProjectInput:
    return ProjectInput(pid, track, team or f"tm_{pid}")


def test_most_uncertain_keeps_one_entry_per_project_nearest_half() -> None:
    a, b = _p("a"), _p("b")
    calls = [CloseCall(a, 3, 0.75), CloseCall(a, 5, 0.52), CloseCall(b, 5, 0.30)]
    ordered = most_uncertain(calls)
    assert [(c.project.id, c.cutoff) for c in ordered] == [("a", 5), ("b", 5)]


def test_prefers_judges_who_reviewed_the_neighbouring_close_calls() -> None:
    a, b, c = _p("a"), _p("b"), _p("c")
    calls = [CloseCall(a, 5, 0.5), CloseCall(b, 5, 0.4), CloseCall(c, 5, 0.3)]
    judges = [JudgeInput("j1"), JudgeInput("j2"), JudgeInput("j3"), JudgeInput("j4")]
    reviewers = {"a": {"j1"}, "b": {"j2", "j3"}, "c": {"j3"}}
    plan = propose(calls, judges, reviewers=reviewers, max_load=10, per_project=1)
    first = plan.proposals[0]
    assert first.project == "a"
    assert first.judge == "j3"  # reviewed both neighbours b and c
    assert first.reason.endswith("this judge also reviewed b, c")


def test_hard_constraints_are_never_broken() -> None:
    a = _p("a", track="t1", team="tm_x")
    calls = [CloseCall(a, 3, 0.5)]
    judges = [
        JudgeInput("reviewed"),
        JudgeInput("conflicted"),
        JudgeInput("other_track", frozenset({"t2"})),
        JudgeInput("full"),
        JudgeInput("flat"),
        JudgeInput("ok", frozenset({"t1"})),
    ]
    plan = propose(
        calls,
        judges,
        reviewers={"a": {"reviewed"}},
        max_load=2,
        load={"full": 2},
        conflicts={("conflicted", "tm_x")},
        exclude={"flat"},
        per_project=2,
    )
    assert [p.judge for p in plan.proposals] == ["ok"]
    assert plan.shortfalls == {"a": 1}


def test_load_then_experience_break_ties_and_the_result_is_reproducible() -> None:
    a = _p("a")
    calls = [CloseCall(a, 5, 0.5)]
    judges = [JudgeInput("busy"), JudgeInput("novice"), JudgeInput("veteran")]
    kwargs = {
        "reviewers": {},
        "max_load": 10,
        "load": {"busy": 5, "novice": 1, "veteran": 1},
        "experience": {"busy": 5, "novice": 1, "veteran": 6},
        "per_project": 2,
    }
    plan = propose(calls, judges, **kwargs)
    assert [p.judge for p in plan.proposals] == ["veteran", "novice"]
    assert propose(calls, judges, **kwargs).proposals == plan.proposals


def test_budget_spends_judge_time_on_the_most_uncertain_first() -> None:
    calls = [CloseCall(_p("sure"), 5, 0.25), CloseCall(_p("coin"), 5, 0.5)]
    judges = [JudgeInput(f"j{i}") for i in range(6)]
    plan = propose(calls, judges, reviewers={}, max_load=10, per_project=2, budget=2)
    assert [p.project for p in plan.proposals] == ["coin", "coin"]
