"""Synthetic events with a known ground truth, used to prove the estimators work.

Each project has a true quality; each judge has a leniency (some harsh, some
generous) and noise; one judge flat-lines; some reviews are missing; a few
judges review only once. The question is simple: which estimator recovers
the true order best?
"""

from dataclasses import dataclass

import numpy as np

from scoring_engine.normalization import Observation


@dataclass(frozen=True)
class SyntheticEvent:
    observations: list[Observation]
    truth: dict[str, float]


def generate(
    seed: int,
    *,
    n_projects: int = 40,
    n_judges: int = 30,
    reviews_per_project: int = 3,
    severity_sd: float = 0.7,
    noise_sd: float = 0.45,
    missing_rate: float = 0.15,
    flat_judges: int = 1,
) -> SyntheticEvent:
    rng = np.random.default_rng(seed)
    quality = rng.normal(3.3, 0.6, n_projects)
    severity = rng.normal(0.0, severity_sd, n_judges)
    projects = [f"p{i:02d}" for i in range(n_projects)]
    judges = [f"j{i:02d}" for i in range(n_judges)]
    load = np.zeros(n_judges, dtype=int)
    observations = []
    for p in range(n_projects):
        # Least-loaded judges first, random tie-break: a connected, balanced design.
        order = sorted(range(n_judges), key=lambda j: (load[j], rng.random()))
        for j in order[:reviews_per_project]:
            load[j] += 1
            if rng.random() < missing_rate:
                continue  # a review nobody finished
            if j < flat_judges:
                score = 4.0
                vector = (4.0, 4.0, 4.0)
            else:
                score = float(np.clip(quality[p] + severity[j] + rng.normal(0, noise_sd), 1, 5))
                vector = None
            observations.append(Observation(judges[j], projects[p], score, vector))
    truth = {projects[p]: float(quality[p]) for p in range(n_projects)}
    return SyntheticEvent(observations, truth)


def spearman(a: dict[str, float], b: dict[str, float]) -> float:
    keys = sorted(set(a) & set(b))
    ra = np.argsort(np.argsort([-a[k] for k in keys]))
    rb = np.argsort(np.argsort([-b[k] for k in keys]))
    return float(np.corrcoef(ra, rb)[0, 1])


def top_k_recall(estimate: dict[str, float], truth: dict[str, float], k: int = 5) -> float:
    top_true = set(sorted(truth, key=lambda p: -truth[p])[:k])
    top_est = set(sorted(estimate, key=lambda p: -estimate[p])[:k])
    return len(top_true & top_est) / k
