"""Cross-judge normalization: three estimators, judge diagnostics, uncertainty and flags.

Input: one observation per (judge, project) = that judge's composite score for
the project (see ``weighting``). Output: an ``Evaluation`` with every
project's score under each method, its rank, a bootstrap interval, the
probability of finishing inside each prize cutoff, and plain-language flags.

Estimators
----------
raw
    Mean of the project's observations. Rewards drawing lenient judges.

shrunk_z
    Per-judge z-score with empirical-Bayes shrinkage of the judge's mean and
    spread toward the panel's (strength ``k`` pseudo-reviews), rescaled to the
    panel scale:  s' = mu_g + sigma_g * (s - mu_j') / sigma_j'.
    Never divides by zero: a judge with one review, or identical scores,
    borrows the panel's spread.

additive (default)
    s_jp = q_p + b_j + e, fitted by ridge-regularised alternating least squares:
        q_p = (sum_j (s_jp - b_j) + lam * mu) / (n_p + lam)
        b_j =  sum_p (s_jp - q_p)             / (n_j + lam)
    q_p is the project's quality, b_j the judge's leniency (positive = generous).
    It never divides by a judge's spread, so a flat-lining judge (identical
    scores on every criterion for every project) cannot break it; their
    observations carry no ranking signal and are left out of the fit (and
    reported). The fit is identifiable because judges overlap on projects.

Uncertainty
-----------
Observations are resampled with replacement within each project (same count),
the chosen estimator is refitted, and the project's rank is recorded. The 5th
and 95th percentiles of its score give a 90% interval; the share of resamples
where it ranks inside a cutoff gives P(top-k).
"""

from collections import defaultdict
from dataclasses import dataclass, field

import numpy as np

METHODS = ("raw", "shrunk_z", "additive")
DEFAULT_METHOD = "additive"


@dataclass(frozen=True)
class Observation:
    judge: str
    project: str
    score: float
    vector: tuple[float, ...] | None = None  # per-criterion scores, when known


@dataclass(frozen=True)
class JudgeDiagnostics:
    judge: str
    n: int
    mean: float
    spread: float
    severity: float  # additive-model leniency b_j; negative = harsh
    flat: bool
    low_n: bool
    misfit: float  # RMS residual under the additive model


@dataclass
class ProjectResult:
    project: str
    n_reviews: int
    informative_reviews: int
    scores: dict[str, float]
    ranks: dict[str, int]
    ci_low: float
    ci_high: float
    p_top: dict[int, float]
    flags: list[str] = field(default_factory=list)

    def rank_movement(self, method: str) -> int:
        """Positive = moved up compared with the raw ranking."""
        return self.ranks["raw"] - self.ranks[method]


@dataclass
class Evaluation:
    method: str
    projects: list[ProjectResult]
    judges: list[JudgeDiagnostics]
    cutoffs: tuple[int, ...]
    params: dict[str, float]
    flags: list[str] = field(default_factory=list)

    def by_project(self) -> dict[str, ProjectResult]:
        return {p.project: p for p in self.projects}

    def ranking(self, method: str | None = None) -> list[str]:
        method = method or self.method
        return [p.project for p in sorted(self.projects, key=lambda p: p.ranks[method])]


# --- Estimators ---------------------------------------------------------------------


def _index(
    obs: list[Observation],
) -> tuple[list[str], list[str], np.ndarray, np.ndarray, np.ndarray]:
    judges = sorted({o.judge for o in obs})
    projects = sorted({o.project for o in obs})
    j_of = {j: i for i, j in enumerate(judges)}
    p_of = {p: i for i, p in enumerate(projects)}
    j_idx = np.array([j_of[o.judge] for o in obs], dtype=np.int64)
    p_idx = np.array([p_of[o.project] for o in obs], dtype=np.int64)
    scores = np.array([o.score for o in obs], dtype=np.float64)
    return judges, projects, j_idx, p_idx, scores


def raw_means(obs: list[Observation]) -> dict[str, float]:
    total: dict[str, list[float]] = defaultdict(list)
    for o in obs:
        total[o.project].append(o.score)
    return {p: float(np.mean(v)) for p, v in total.items()}


def shrunk_z(obs: list[Observation], k: float = 3.0) -> dict[str, float]:
    judges, projects, j_idx, p_idx, s = _index(obs)
    mu_g = float(s.mean())
    sigma_g = float(s.std()) or 1.0
    n_j = np.bincount(j_idx, minlength=len(judges)).astype(float)
    mean_j = np.bincount(j_idx, weights=s, minlength=len(judges)) / n_j
    var_j = np.bincount(j_idx, weights=(s - mean_j[j_idx]) ** 2, minlength=len(judges)) / n_j
    mu_shrunk = (n_j * mean_j + k * mu_g) / (n_j + k)
    sigma_shrunk = np.sqrt((n_j * var_j + k * sigma_g**2) / (n_j + k))
    adjusted = mu_g + sigma_g * (s - mu_shrunk[j_idx]) / sigma_shrunk[j_idx]
    n_p = np.bincount(p_idx, minlength=len(projects))
    means = np.bincount(p_idx, weights=adjusted, minlength=len(projects)) / n_p
    return {p: float(means[i]) for i, p in enumerate(projects)}


def additive(
    obs: list[Observation], lam: float = 1.0, iterations: int = 200, tol: float = 1e-10
) -> tuple[dict[str, float], dict[str, float]]:
    """Return (project quality q_p, judge leniency b_j)."""
    judges, projects, j_idx, p_idx, s = _index(obs)
    mu = float(s.mean())
    n_p = np.bincount(p_idx, minlength=len(projects)).astype(float)
    n_j = np.bincount(j_idx, minlength=len(judges)).astype(float)
    q = np.full(len(projects), mu)
    b = np.zeros(len(judges))
    for _ in range(iterations):
        q_new = (np.bincount(p_idx, weights=s - b[j_idx], minlength=len(projects)) + lam * mu) / (
            n_p + lam
        )
        b_new = np.bincount(j_idx, weights=s - q_new[p_idx], minlength=len(judges)) / (n_j + lam)
        delta = max(float(np.max(np.abs(q_new - q))), float(np.max(np.abs(b_new - b))))
        q, b = q_new, b_new
        if delta < tol:
            break
    return (
        {p: float(q[i]) for i, p in enumerate(projects)},
        {j: float(b[i]) for i, j in enumerate(judges)},
    )


def naive_z(obs: list[Observation]) -> dict[str, float]:
    """The textbook per-judge z-score, kept only to show where it breaks (NaN on zero spread)."""
    by_judge: dict[str, list[float]] = defaultdict(list)
    for o in obs:
        by_judge[o.judge].append(o.score)
    stats = {j: (float(np.mean(v)), float(np.std(v))) for j, v in by_judge.items()}
    per_project: dict[str, list[float]] = defaultdict(list)
    for o in obs:
        mean, sd = stats[o.judge]
        per_project[o.project].append((o.score - mean) / sd if sd > 0 else float("nan"))
    return {p: float(np.mean(v)) for p, v in per_project.items()}


# --- Evaluation -------------------------------------------------------------


def _ranks(scores: dict[str, float]) -> dict[str, int]:
    order = sorted(scores, key=lambda p: (-scores[p], p))
    return {p: i + 1 for i, p in enumerate(order)}


def flat_judges(obs: list[Observation]) -> set[str]:
    """Judges who gave every project the same scores on every criterion.

    Equal composites alone are not enough: 5+4+2 and 4+3+4 are different
    judgements that happen to add up the same. Only identical per-criterion
    scores (or, when those are unknown, identical composites) mark a judge
    whose reviews carry no ranking signal.
    """
    by_judge: dict[str, list[Observation]] = defaultdict(list)
    for o in obs:
        by_judge[o.judge].append(o)
    flat = set()
    for judge, items in by_judge.items():
        if len(items) < 2:
            continue
        if all(o.vector is not None for o in items):
            if len({o.vector for o in items}) == 1:
                flat.add(judge)
        elif float(np.ptp([o.score for o in items])) == 0.0:
            flat.add(judge)
    return flat


def _estimate(method: str, obs: list[Observation], k: float, lam: float) -> dict[str, float]:
    if method == "raw":
        return raw_means(obs)
    if method == "shrunk_z":
        return shrunk_z(obs, k)
    return additive(obs, lam)[0]


def evaluate(
    observations: list[Observation],
    *,
    method: str = DEFAULT_METHOD,
    cutoffs: tuple[int, ...] = (1, 2, 3, 5),
    bootstrap: int = 300,
    seed: int = 0,
    k: float = 3.0,
    lam: float = 1.0,
    close_low: float = 0.2,
    close_high: float = 0.8,
) -> Evaluation:
    if method not in METHODS:
        raise ValueError(f"unknown method {method!r}")
    if not observations:
        return Evaluation(method, [], [], cutoffs, {"k": k, "lambda": lam})

    # A canonical order makes the seeded bootstrap independent of how the caller
    # happened to list the reviews (database order, file order, ...).
    observations = sorted(observations, key=lambda o: (o.project, o.judge, o.score))
    flat = flat_judges(observations)
    fit_obs = [o for o in observations if o.judge not in flat] or list(observations)
    raw = raw_means(observations)
    zs = shrunk_z(fit_obs, k)
    quality, leniency = additive(fit_obs, lam)
    # Projects reviewed only by flat-lining judges fall back to their raw mean.
    for project, value in raw.items():
        zs.setdefault(project, value)
        quality.setdefault(project, value)
    estimates = {"raw": raw, "shrunk_z": zs, "additive": quality}
    ranks = {m: _ranks(estimates[m]) for m in METHODS}

    # Bootstrap the chosen method.
    rng = np.random.default_rng(seed)
    by_project: dict[str, list[Observation]] = defaultdict(list)
    for o in fit_obs if method != "raw" else observations:
        by_project[o.project].append(o)
    projects = sorted(raw)
    samples: dict[str, list[float]] = defaultdict(list)
    top_counts: dict[str, dict[int, int]] = {p: dict.fromkeys(cutoffs, 0) for p in projects}
    for _ in range(bootstrap):
        resampled: list[Observation] = []
        for items in by_project.values():
            picks = rng.integers(0, len(items), size=len(items))
            resampled.extend(items[i] for i in picks)
        estimate = _estimate(method, resampled, k, lam)
        for project in projects:
            estimate.setdefault(project, raw[project])
        sample_ranks = _ranks(estimate)
        for project in projects:
            samples[project].append(estimate[project])
            for cutoff in cutoffs:
                if sample_ranks[project] <= cutoff:
                    top_counts[project][cutoff] += 1

    reviews = defaultdict(int)
    informative = defaultdict(int)
    for o in observations:
        reviews[o.project] += 1
        if o.judge not in flat:
            informative[o.project] += 1

    results = []
    for project in projects:
        values = np.array(samples[project]) if samples[project] else np.array([raw[project]])
        p_top = {c: top_counts[project][c] / bootstrap if bootstrap else 0.0 for c in cutoffs}
        flags = []
        if informative[project] < 2:
            flags.append("weak_evidence")
        for cutoff in cutoffs:
            if close_low < p_top[cutoff] < close_high:
                flags.append(f"close_call_top{cutoff}")
        results.append(
            ProjectResult(
                project=project,
                n_reviews=reviews[project],
                informative_reviews=informative[project],
                scores={m: estimates[m][project] for m in METHODS},
                ranks={m: ranks[m][project] for m in METHODS},
                ci_low=float(np.percentile(values, 5)),
                ci_high=float(np.percentile(values, 95)),
                p_top=p_top,
                flags=flags,
            )
        )
    results.sort(key=lambda r: r.ranks[method])

    # Judge diagnostics under the additive model.
    by_judge: dict[str, list[Observation]] = defaultdict(list)
    for o in observations:
        by_judge[o.judge].append(o)
    judges = []
    for judge, items in sorted(by_judge.items()):
        values = np.array([o.score for o in items])
        residuals = [o.score - quality[o.project] - leniency.get(judge, 0.0) for o in items]
        judges.append(
            JudgeDiagnostics(
                judge=judge,
                n=len(items),
                mean=float(values.mean()),
                spread=float(values.std()),
                severity=leniency.get(judge, 0.0),
                flat=judge in flat,
                low_n=len(items) < 3,
                misfit=float(np.sqrt(np.mean(np.square(residuals)))),
            )
        )

    evaluation_flags = []
    for cutoff in cutoffs:
        top_default = {p for p, r in ranks[method].items() if r <= cutoff}
        top_other = {p for p, r in ranks["shrunk_z"].items() if r <= cutoff}
        if method != "shrunk_z" and top_default != top_other:
            evaluation_flags.append(f"method_disagreement_top{cutoff}")
    return Evaluation(
        method=method,
        projects=results,
        judges=judges,
        cutoffs=cutoffs,
        params={"k": k, "lambda": lam, "bootstrap": bootstrap, "seed": seed},
        flags=evaluation_flags,
    )
