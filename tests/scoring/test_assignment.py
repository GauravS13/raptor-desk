from collections import Counter

from hypothesis import given, settings
from hypothesis import strategies as st

from scoring_engine.assignment import JudgeInput, ProjectInput, plan


def small_event() -> tuple[list[ProjectInput], list[JudgeInput]]:
    projects = [ProjectInput(f"p{i}", "a" if i % 2 else "b", f"t{i}") for i in range(8)]
    judges = [
        JudgeInput("j1", frozenset({"a"})),
        JudgeInput("j2", frozenset({"a"})),
        JudgeInput("j3", frozenset({"a", "b"})),
        JudgeInput("j4", frozenset({"b"})),
        JudgeInput("j5", frozenset({"b"})),
    ]
    return projects, judges


def test_every_project_gets_its_reviews_within_track_and_load() -> None:
    projects, judges = small_event()
    result = plan(projects, judges, reviews_per_project=2, max_load=5)
    assert result.shortfalls == {}
    per_project = Counter(p.project for p in result.proposals)
    assert set(per_project.values()) == {2}
    tracks = {j.id: j.tracks for j in judges}
    track_of = {p.id: p.track for p in projects}
    assert all(track_of[p.project] in tracks[p.judge] for p in result.proposals)
    assert max(Counter(p.judge for p in result.proposals).values()) <= 5


def test_conflicts_and_existing_assignments_are_respected() -> None:
    projects, judges = small_event()
    conflicts = {("j3", "t1")}
    existing = {("j1", "p1")}
    result = plan(
        projects, judges, reviews_per_project=2, max_load=6, existing=existing, conflicts=conflicts
    )
    pairs = {(p.judge, p.project) for p in result.proposals}
    assert ("j3", "p1") not in pairs
    assert ("j1", "p1") not in pairs  # already assigned, not proposed again
    assert sum(1 for p in result.proposals if p.project == "p1") == 1


def test_impossible_coverage_is_reported_not_dropped() -> None:
    projects = [ProjectInput("p1", "rare", "t1")]
    judges = [JudgeInput("j1", frozenset({"rare"}))]
    result = plan(projects, judges, reviews_per_project=3, max_load=5)
    assert result.shortfalls == {"p1": 2}


def test_plans_are_reproducible() -> None:
    projects, judges = small_event()
    first = plan(projects, judges, reviews_per_project=2, max_load=5)
    second = plan(projects, judges, reviews_per_project=2, max_load=5)
    assert first.proposals == second.proposals


def test_load_is_balanced_when_tracks_allow() -> None:
    projects = [ProjectInput(f"p{i}", None, f"t{i}") for i in range(12)]
    judges = [JudgeInput(f"j{i}") for i in range(4)]
    result = plan(projects, judges, reviews_per_project=2, max_load=20)
    loads = Counter(p.judge for p in result.proposals).values()
    assert max(loads) - min(loads) <= 1


@settings(max_examples=60, deadline=None)
@given(
    n_projects=st.integers(1, 15),
    n_judges=st.integers(1, 8),
    k=st.integers(1, 4),
    max_load=st.integers(1, 10),
)
def test_hard_constraints_always_hold(
    n_projects: int, n_judges: int, k: int, max_load: int
) -> None:
    projects = [ProjectInput(f"p{i}", ["a", "b"][i % 2], f"t{i}") for i in range(n_projects)]
    judges = [JudgeInput(f"j{i}", frozenset({["a", "b"][i % 2]})) for i in range(n_judges)]
    result = plan(projects, judges, reviews_per_project=k, max_load=max_load)
    pairs = [(p.judge, p.project) for p in result.proposals]
    assert len(pairs) == len(set(pairs))
    assert all(v <= max_load for v in Counter(j for j, _ in pairs).values())
    placed = Counter(p for _, p in pairs)
    for project in projects:
        assert placed[project.id] + result.shortfalls.get(project.id, 0) == k
