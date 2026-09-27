"""Compute an event's results with the Fairness Engine.

Each judge's latest submitted review of each project becomes one observation
(its weighted composite, plus the per-criterion vector so a rubber-stamping
judge can be recognised). Projects failing a gate are listed but not ranked.
Bonus criteria never change a score; they only break exact ties.
"""

from collections import defaultdict
from dataclasses import dataclass
from statistics import mean

from apps.events.models import Event
from apps.judging import repositories
from apps.judging.scoring import engine_criteria, latest_reviews, score_map
from apps.submissions.models import Project
from scoring_engine import weighting
from scoring_engine.normalization import METHODS, Evaluation, Observation, evaluate

DEFAULT_CUTOFFS = (1, 2, 3, 5)


@dataclass
class EventResults:
    event: Event
    evaluation: Evaluation
    names: dict[str, str]
    teams: dict[str, str]
    gated_out: list[str]
    bonus: dict[str, float]
    observations: list[Observation]

    @property
    def method(self) -> str:
        return self.evaluation.method


def compute(
    event: Event, method: str = "additive", bootstrap: int = 300, seed: int = 0
) -> EventResults:
    if method not in METHODS:
        method = "additive"
    criteria = engine_criteria(event)
    scored_keys = [c.key for c in criteria if c.is_scored]
    reviews = latest_reviews(list(repositories.reviews_for_organizer(event.pk)))

    per_project_scores: dict[str, list[dict[str, float]]] = defaultdict(list)
    observations: list[Observation] = []
    for review in reviews:
        scores = score_map(review)
        per_project_scores[review.project_id].append(scores)
        composite = weighting.composite(scores, criteria)
        if composite is None:
            continue
        vector = tuple(scores.get(key, float("nan")) for key in scored_keys)
        observations.append(Observation(review.judge_id, review.project_id, composite, vector))

    # Gate: a project whose average gate score is below the threshold is not ranked.
    gated_out = []
    for project_id, score_list in per_project_scores.items():
        averaged = {}
        for criterion in criteria:
            if criterion.is_gate:
                values = [s[criterion.key] for s in score_list if criterion.key in s]
                if values:
                    averaged[criterion.key] = mean(values)
        if averaged and not weighting.passes_gates(averaged, criteria):
            gated_out.append(project_id)
    ranked_obs = [o for o in observations if o.project not in gated_out]

    evaluation = evaluate(
        ranked_obs, method=method, cutoffs=DEFAULT_CUTOFFS, bootstrap=bootstrap, seed=seed
    )

    bonus = {
        project_id: mean(weighting.bonus_points(s, criteria) for s in score_list)
        for project_id, score_list in per_project_scores.items()
    }
    # Exact ties on the chosen method are broken by bonus points, then by id.
    evaluation.projects.sort(
        key=lambda p: (-round(p.scores[method], 9), -bonus.get(p.project, 0.0), p.project)
    )
    for position, project in enumerate(evaluation.projects, start=1):
        project.ranks[method] = position

    projects = Project.objects.filter(event=event).select_related("canonical_version", "team")
    names = {p.pk: p.canonical_version.name if p.canonical_version else p.pk for p in projects}
    teams = {p.pk: p.team.name for p in projects}
    return EventResults(event, evaluation, names, teams, sorted(gated_out), bonus, observations)


def as_rows(results: EventResults) -> list[dict[str, object]]:
    method = results.method
    rows = []
    for project in results.evaluation.projects:
        rows.append(
            {
                "rank": project.ranks[method],
                "project_id": project.project,
                "project": results.names.get(project.project, project.project),
                "team": results.teams.get(project.project, ""),
                "reviews": project.n_reviews,
                "informative_reviews": project.informative_reviews,
                "raw": project.scores["raw"],
                "raw_rank": project.ranks["raw"],
                "shrunk_z": project.scores["shrunk_z"],
                "additive": project.scores["additive"],
                "score": project.scores[method],
                "movement": project.ranks["raw"] - project.ranks[method],
                "ci_low": project.ci_low,
                "ci_high": project.ci_high,
                "p_top": project.p_top,
                "bonus": results.bonus.get(project.project, 0.0),
                "flags": project.flags,
            }
        )
    return rows
