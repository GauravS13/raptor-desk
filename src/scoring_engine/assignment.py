"""Plan judge assignments: coverage first, fairness second, reproducible always.

Hard constraints (never broken):
  * a judge only reviews projects in tracks they cover (a judge with no tracks covers all);
  * never a project whose team the judge has a conflict with;
  * never the same project twice;
  * never more than ``max_load`` assignments per judge.

Soft preferences, in order:
  1. projects with the fewest eligible judges are placed first, so scarce
     specialists are not used up by easy projects;
  2. the least-loaded eligible judge is chosen;
  3. judges who have shared fewest projects with the project's other judges
     are preferred, which keeps the judge network connected (the bias model
     needs judges linked through common projects);
  4. remaining ties break on a stable hash of (judge, project), so the same
     input always gives the same plan.

Anything that cannot be placed is reported as a shortfall, never dropped.
"""

import hashlib
from collections import defaultdict
from dataclasses import dataclass, field


@dataclass(frozen=True)
class ProjectInput:
    id: str
    track: str | None
    team: str


@dataclass(frozen=True)
class JudgeInput:
    id: str
    tracks: frozenset[str] = frozenset()


@dataclass(frozen=True)
class Proposal:
    judge: str
    project: str
    reason: str


@dataclass
class Plan:
    proposals: list[Proposal] = field(default_factory=list)
    shortfalls: dict[str, int] = field(default_factory=dict)


def _tie(judge: str, project: str) -> str:
    return hashlib.sha256(f"{judge}|{project}".encode()).hexdigest()


def eligible(judge: JudgeInput, project: ProjectInput, conflicts: set[tuple[str, str]]) -> bool:
    if (judge.id, project.team) in conflicts:
        return False
    return not judge.tracks or (project.track is not None and project.track in judge.tracks)


def plan(
    projects: list[ProjectInput],
    judges: list[JudgeInput],
    *,
    reviews_per_project: int,
    max_load: int,
    existing: set[tuple[str, str]] | None = None,
    conflicts: set[tuple[str, str]] | None = None,
) -> Plan:
    existing = set(existing or set())
    conflicts = set(conflicts or set())
    load: dict[str, int] = defaultdict(int)
    reviewers: dict[str, set[str]] = defaultdict(set)
    for judge_id, project_id in existing:
        load[judge_id] += 1
        reviewers[project_id].add(judge_id)
    shared: dict[tuple[str, str], int] = defaultdict(int)
    for members in reviewers.values():
        for a in members:
            for b in members:
                if a != b:
                    shared[(a, b)] += 1

    def candidates(project: ProjectInput) -> list[JudgeInput]:
        return [
            j
            for j in judges
            if eligible(j, project, conflicts)
            and j.id not in reviewers[project.id]
            and load[j.id] < max_load
        ]

    result = Plan()
    order = sorted(projects, key=lambda p: (len(candidates(p)), p.id))
    for project in order:
        needed = reviews_per_project - len(reviewers[project.id])
        while needed > 0:
            pool = candidates(project)
            if not pool:
                result.shortfalls[project.id] = needed
                break
            chosen = min(
                pool,
                key=lambda j: (
                    load[j.id],
                    sum(shared[(j.id, other)] for other in reviewers[project.id]),
                    _tie(j.id, project.id),
                ),
            )
            for other in reviewers[project.id]:
                shared[(chosen.id, other)] += 1
                shared[(other, chosen.id)] += 1
            reviewers[project.id].add(chosen.id)
            load[chosen.id] += 1
            track = f"track {project.track}" if project.track else "no track"
            result.proposals.append(
                Proposal(
                    judge=chosen.id,
                    project=project.id,
                    reason=f"{track} match, load {load[chosen.id]}/{max_load}",
                )
            )
            needed -= 1
    return result
