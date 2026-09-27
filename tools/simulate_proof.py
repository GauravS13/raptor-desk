"""Reproduce every number in JUDGING.md: fixture results, simulation proof, sensitivity.

    uv run python tools/simulate_proof.py            # prints markdown, writes docs/img/*.svg

Deterministic: fixed seeds throughout, so the output is identical on every run.
"""

import json
import math
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from scoring_engine import simulate  # noqa: E402
from scoring_engine.normalization import (  # noqa: E402
    Observation,
    additive,
    evaluate,
    flat_judges,
    naive_z,
    raw_means,
    shrunk_z,
)

RUNS = 200


def fixture_observations() -> list[Observation]:
    data = json.loads((ROOT / "data" / "fixtures.json").read_text(encoding="utf-8"))
    latest: dict[tuple[str, str], tuple[float, tuple[int, ...]]] = {}
    for score in data["scores"]:
        project = "prj_07" if score["project"] == "prj_41" else score["project"]
        c = score["criteria"]
        vector = (c["functionality"], c["quality"], c["innovation"])
        latest[(score["judge"], project)] = (sum(vector) / 3, vector)
    return [Observation(j, p, v, vec) for (j, p), (v, vec) in latest.items()]


def fixture_section() -> str:
    obs = fixture_observations()
    result = evaluate(obs, bootstrap=300, seed=0)
    by = result.by_project()
    broken = sorted(p for p, v in naive_z(obs).items() if math.isnan(v))
    lines = ["### Results on the official fixtures", ""]
    lines.append(f"- Observations (latest review per judge and project): {len(obs)}")
    lines.append(f"- Judges with identical scores on every criterion: {sorted(flat_judges(obs))}")
    lines.append(
        f"- Projects where the textbook z-score is NaN: {len(broken)} ({', '.join(broken)})"
    )
    lines.append(f"- Flags on the ranking: {', '.join(result.flags) or 'none'}")
    lines.append("")
    lines.append(
        "| Rank | Project | Raw mean (rank) | Shrunken z | Additive | Move | 90% interval | P(top 5) |"
    )
    lines.append("|---|---|---|---|---|---|---|---|")
    for p in result.projects[:10]:
        move = p.ranks["raw"] - p.ranks["additive"]
        arrow = f"▲{move}" if move > 0 else (f"▼{-move}" if move < 0 else "–")
        lines.append(
            f"| {p.ranks['additive']} | {p.project} | {p.scores['raw']:.3f} ({p.ranks['raw']}) | "
            f"{p.scores['shrunk_z']:.3f} | {p.scores['additive']:.3f} | {arrow} | "
            f"{p.ci_low:.2f}–{p.ci_high:.2f} | {p.p_top[5]:.2f} |"
        )
    prj10 = by["prj_10"]
    lines.append("")
    lines.append(
        f"prj_10: raw rank {prj10.ranks['raw']}, shrunken z rank {prj10.ranks['shrunk_z']}, "
        f"additive rank {prj10.ranks['additive']}."
    )
    judges = sorted(result.judges, key=lambda j: j.severity)
    lines.append("")
    lines.append(
        "Harshest judges: "
        + ", ".join(f"{j.judge} ({j.severity:+.2f}, n={j.n})" for j in judges[:3])
    )
    lines.append(
        "Most lenient: "
        + ", ".join(f"{j.judge} ({j.severity:+.2f}, n={j.n})" for j in judges[::-1][:3])
    )
    return "\n".join(lines)


def run_simulation() -> dict[str, dict[str, list[float]]]:
    stats: dict[str, dict[str, list[float]]] = {
        m: {"rho": [], "top5": []} for m in ("raw", "shrunk_z", "additive")
    }
    for seed in range(RUNS):
        event = simulate.generate(seed)
        raw = raw_means(event.observations)
        fit = [o for o in event.observations if o.judge not in flat_judges(event.observations)]
        estimates = {
            "raw": raw,
            "shrunk_z": {**raw, **shrunk_z(fit)},
            "additive": {**raw, **additive(fit)[0]},
        }
        for method, estimate in estimates.items():
            stats[method]["rho"].append(simulate.spearman(estimate, event.truth))
            stats[method]["top5"].append(simulate.top_k_recall(estimate, event.truth, 5))
    return stats


def simulation_section(stats: dict[str, dict[str, list[float]]]) -> str:
    lines = [f"### Simulation: {RUNS} synthetic events with a known true order", ""]
    lines.append(
        "| Method | Mean Spearman ρ with the truth | Top-5 recall | Runs where it beats raw (ρ) |"
    )
    lines.append("|---|---|---|---|")
    raw_rho = np.array(stats["raw"]["rho"])
    for method in ("raw", "shrunk_z", "additive"):
        rho = np.array(stats[method]["rho"])
        wins = "–" if method == "raw" else f"{np.mean(rho > raw_rho) * 100:.0f}%"
        lines.append(
            f"| {method} | {rho.mean():.3f} (sd {rho.std():.3f}) | "
            f"{np.mean(stats[method]['top5']):.3f} | {wins} |"
        )
    return "\n".join(lines)


def sensitivity_section() -> str:
    obs = fixture_observations()
    base = evaluate(obs, bootstrap=0).ranking("additive")[:5]
    lines = ["### Sensitivity on the fixtures", ""]
    lines.append("| Parameter | Value | Additive top 5 | Same set as default? |")
    lines.append("|---|---|---|---|")
    for lam in (0.3, 1.0, 3.0):
        top = evaluate(obs, bootstrap=0, lam=lam).ranking("additive")[:5]
        lines.append(
            f"| λ | {lam} | {', '.join(top)} | {'yes' if set(top) == set(base) else 'no'} |"
        )
    for k in (1.0, 3.0, 10.0):
        top = evaluate(obs, bootstrap=0, k=k).ranking("shrunk_z")[:5]
        lines.append(
            f"| k (shrunken z) | {k} | {', '.join(top)} | {'yes' if set(top) == set(base) else 'no'} |"
        )
    return "\n".join(lines)


def bar_chart_svg(stats: dict[str, dict[str, list[float]]], path: Path) -> None:
    methods = [("raw", "Raw mean"), ("shrunk_z", "Shrunken z"), ("additive", "Additive model")]
    width, height, left, bar_h, gap = 560, 170, 150, 30, 18
    rows = []
    for i, (key, label) in enumerate(methods):
        value = float(np.mean(stats[key]["rho"]))
        y = 30 + i * (bar_h + gap)
        w = (width - left - 60) * value
        rows.append(
            f'<text x="{left - 10}" y="{y + 20}" text-anchor="end">{label}</text>'
            f'<rect x="{left}" y="{y}" width="{w:.1f}" height="{bar_h}" rx="3" class="bar-{key}"/>'
            f'<text x="{left + w + 8:.1f}" y="{y + 20}">{value:.3f}</text>'
        )
    svg = (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}" role="img" '
        f'aria-labelledby="t"><title id="t">Mean Spearman correlation with the true order over '
        f"{RUNS} simulated events</title>"
        "<style>text{font:14px sans-serif;fill:#333}.bar-raw{fill:#9aa5b1}"
        ".bar-shrunk_z{fill:#5b8def}.bar-additive{fill:#1f6f43}</style>"
        f'<text x="{left}" y="18">Mean Spearman ρ with the truth ({RUNS} runs; higher is better)</text>'
        + "".join(rows)
        + "</svg>\n"
    )
    path.write_text(svg, encoding="utf-8")


def rank_movement_svg(path: Path) -> None:
    result = evaluate(fixture_observations(), bootstrap=0)
    top = [p for p in result.projects if p.ranks["raw"] <= 10 or p.ranks["additive"] <= 10]
    width, height, x1, x2, top_y, step = 460, 440, 130, 330, 40, 38
    parts = []
    for p in top:
        y1 = top_y + (p.ranks["raw"] - 1) * step
        y2 = top_y + (p.ranks["additive"] - 1) * step
        colour = (
            "#1f6f43"
            if p.ranks["additive"] < p.ranks["raw"]
            else ("#b3261e" if p.ranks["additive"] > p.ranks["raw"] else "#9aa5b1")
        )
        if p.ranks["raw"] <= 10:
            parts.append(
                f'<text x="{x1 - 8}" y="{y1 + 5}" text-anchor="end">{p.ranks["raw"]}. {p.project}</text>'
            )
        if p.ranks["additive"] <= 10:
            parts.append(
                f'<text x="{x2 + 8}" y="{y2 + 5}">{p.ranks["additive"]}. {p.project}</text>'
            )
        y2c = min(y2, top_y + 10 * step)
        y1c = min(y1, top_y + 10 * step)
        parts.append(
            f'<line x1="{x1}" y1="{y1c}" x2="{x2}" y2="{y2c}" stroke="{colour}" stroke-width="2.5"/>'
        )
    svg = (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}" role="img" '
        'aria-labelledby="t2"><title id="t2">Rank movement on the fixtures: raw mean to the '
        "additive judge-bias model</title><style>text{font:13px sans-serif;fill:#333}</style>"
        f'<text x="{x1}" y="20" text-anchor="end">Raw mean</text>'
        f'<text x="{x2}" y="20">Bias-corrected</text>' + "".join(parts) + "</svg>\n"
    )
    path.write_text(svg, encoding="utf-8")


def main() -> None:
    img = ROOT / "docs" / "img"
    img.mkdir(parents=True, exist_ok=True)
    stats = run_simulation()
    bar_chart_svg(stats, img / "normalization-proof.svg")
    rank_movement_svg(img / "rank-movement.svg")
    print(fixture_section())
    print()
    print(simulation_section(stats))
    print()
    print(sensitivity_section())


if __name__ == "__main__":
    main()
