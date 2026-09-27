"""Ask: turn the ranking's doubt into the few extra reviews that would resolve it.

A close call is a project whose chance of finishing inside a prize cutoff is
neither near 0 nor near 1 (see ``normalization.evaluate``). More evidence about
exactly those projects is what separates them; more evidence anywhere else
changes nothing a prize depends on.

For each close call, most uncertain first (chance nearest 0.5), the desk picks
``per_project`` judges.

Hard constraints (never broken):
  * the judge is eligible: covers the track, has no conflict with the team;
  * has not reviewed the project already;
  * is under ``max_load``;
  * is not excluded (for example a judge whose scores carry no signal).

Soft preferences, in order:
  1. judges who already reviewed other projects in the same close-call band:
     their direct comparison is what separates neighbours;
  2. the least-loaded judge;
  3. the judge with the most reviews overall, whose leniency is best measured;
  4. a stable hash, so the same input always gives the same proposal.
"""

from collections import defaultdict
from dataclasses import dataclass

from scoring_engine.assignment import JudgeInput, Plan, ProjectInput, Proposal, _tie, eligible


@dataclass(frozen=True)
class CloseCall:
    project: ProjectInput
    cutoff: int
    p: float  # chance of finishing inside the cutoff


def most_uncertain(close_calls: list[CloseCall]) -> list[CloseCall]:
    """One entry per project (its most uncertain cutoff), most uncertain first."""
    best: dict[str, CloseCall] = {}
    for call in close_calls:
        current = best.get(call.project.id)
        if current is None or abs(call.p - 0.5) < abs(current.p - 0.5):
            best[call.project.id] = call
    return sorted(best.values(), key=lambda c: (abs(c.p - 0.5), c.cutoff, c.project.id))


def propose(
    close_calls: list[CloseCall],
    judges: list[JudgeInput],
    *,
    reviewers: dict[str, set[str]],
    max_load: int,
    load: dict[str, int] | None = None,
    experience: dict[str, int] | None = None,
    conflicts: set[tuple[str, str]] | None = None,
    exclude: set[str] | None = None,
    per_project: int = 2,
) -> Plan:
    reviewers = {p: set(js) for p, js in reviewers.items()}
    load = defaultdict(int, load or {})
    experience = experience or {}
    conflicts = conflicts or set()
    exclude = exclude or set()

    band: dict[int, set[str]] = defaultdict(set)
    for call in close_calls:
        band[call.cutoff].add(call.project.id)

    result = Plan()
    for call in most_uncertain(close_calls):
        project = call.project
        neighbours = band[call.cutoff] - {project.id}
        for _ in range(per_project):
            pool = [
                j
                for j in judges
                if j.id not in exclude
                and eligible(j, project, conflicts)
                and j.id not in reviewers.setdefault(project.id, set())
                and load[j.id] < max_load
            ]
            if not pool:
                result.shortfalls[project.id] = result.shortfalls.get(project.id, 0) + 1
                continue

            def overlap(judge: JudgeInput, near: set[str] = neighbours) -> list[str]:
                return sorted(n for n in near if judge.id in reviewers.get(n, set()))

            chosen = min(
                pool,
                key=lambda j: (
                    -len(overlap(j)),
                    load[j.id],
                    -experience.get(j.id, 0),
                    _tie(j.id, project.id),
                ),
            )
            shared = overlap(chosen)
            reason = f"close call for the top {call.cutoff} (chance {call.p:.2f})"
            if shared:
                reason += f"; this judge also reviewed {', '.join(shared)}"
            result.proposals.append(Proposal(judge=chosen.id, project=project.id, reason=reason))
            reviewers[project.id].add(chosen.id)
            load[chosen.id] += 1
    return result
