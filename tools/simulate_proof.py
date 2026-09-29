"""Reproduce every number in JUDGING.md: fixture results, simulation proof, sensitivity.

    uv run python tools/simulate_proof.py            # rewrites the generated blocks and SVGs
    uv run python tools/simulate_proof.py --check    # exit 1 if JUDGING.md is out of date

The numbers live between ``<!-- generated:NAME -->`` and ``<!-- /generated:NAME -->``
markers in JUDGING.md and are never edited by hand. Deterministic: fixed seeds
throughout, so the output is identical on every run.
"""

import argparse
import json
import math
import re
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from scoring_engine import simulate  # noqa: E402
from scoring_engine.calibration import Calibration, calibrate  # noqa: E402
from scoring_engine.influence import kingmakers  # noqa: E402
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


FLAG_LABELS = {"method_disagreement_top": "methods disagree on the top "}


def _flag(flag: str) -> str:
    for prefix, label in FLAG_LABELS.items():
        if flag.startswith(prefix):
            return f"**{label}{flag.removeprefix(prefix)}**"
    return flag


def fixture_section() -> str:
    obs = fixture_observations()
    result = evaluate(obs, bootstrap=300, seed=0)
    broken = sorted(p for p, v in naive_z(obs).items() if math.isnan(v))
    flat = ", ".join(sorted(flat_judges(obs)))
    lines = [f"- Observations (latest review per judge and project): {len(obs)}"]
    lines.append(f"- Judges with identical scores on every criterion: {flat}")
    lines.append(
        f"- Projects where the textbook z-score is NaN: {len(broken)} ({', '.join(broken)})"
    )
    flags = ", ".join(_flag(f) for f in result.flags) or "none"
    lines.append(f"- Flags on the ranking: {flags}")
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
    labels = {"raw": "raw mean", "shrunk_z": "shrunken z", "additive": "**additive**"}
    lines = [
        f"| Method | Mean Spearman ρ with the truth ({RUNS} runs) | Top-5 recall "
        "| Runs where it beats raw |",
        "|---|---|---|---|",
    ]
    raw_rho = np.array(stats["raw"]["rho"])
    for method in ("raw", "shrunk_z", "additive"):
        rho = np.array(stats[method]["rho"])
        wins = "–" if method == "raw" else f"{np.mean(rho > raw_rho) * 100:.0f}%"
        mean, recall = f"{rho.mean():.3f}", f"{np.mean(stats[method]['top5']):.3f}"
        if method == "additive":
            mean, recall, wins = f"**{mean}**", f"**{recall}**", f"**{wins}**"
        lines.append(f"| {labels[method]} | {mean} (sd {rho.std():.3f}) | {recall} | {wins} |")
    return "\n".join(lines)


def sensitivity_section() -> str:
    obs = fixture_observations()
    base = evaluate(obs, bootstrap=0).ranking("additive")[:5]
    lines = ["| Parameter | Value | Top 5 | Same set as the default? |", "|---|---|---|---|"]
    rows = [("λ (additive)", lam, 1.0, {"lam": lam}, "additive") for lam in (0.3, 1.0, 3.0)]
    rows += [("k (shrunken z)", k, 3.0, {"k": k}, "shrunk_z") for k in (1.0, 3.0, 10.0)]
    for name, value, default, params, method in rows:
        top = evaluate(obs, bootstrap=0, **params).ranking(method)[:5]
        shown = f"**{value:g}**" if value == default else f"{value:g}"
        same = "yes" if set(top) == set(base) else "no"
        lines.append(f"| {name} | {shown} | {', '.join(top)} | {same} |")
    return "\n".join(lines)


def kingmaker_section() -> str:
    found = kingmakers(fixture_observations(), places=5)
    if not found:
        return "No single judge decides a prize place on the fixtures."
    lines = [
        "| Judge | Reviews | Without this judge, enters the top 5 | Leaves the top 5 | Winner |",
        "|---|---|---|---|---|",
    ]
    for item in found:
        winner = (
            f"**{item.winner_before} → {item.winner_after}**"
            if item.changes_winner
            else f"{item.winner_before} (unchanged)"
        )
        lines.append(
            f"| {item.judge} | {item.reviews} | {', '.join(item.entered) or '–'} | "
            f"{', '.join(item.left) or '–'} | {winner} |"
        )
    judges = len({o.judge for o in fixture_observations()})
    lines.append("")
    lines.append(
        f"{len(found)} of {judges} judges are kingmakers on the top 5; "
        f"{sum(i.changes_winner for i in found)} of them alone decide first place."
    )
    return "\n".join(lines)


def calibration_section(cal: Calibration) -> str:
    by_reviews = ", ".join(
        f"{share * 100:.0f}% with {n} informative review{'' if n == '1' else 's'}"
        for n, share in cal.coverage_by_reviews.items()
    )
    lines = [
        f"- Projects checked: {cal.projects} across {cal.runs} synthetic events",
        f"- **90% intervals that contain the true quality: {cal.coverage * 100:.0f}%** "
        f"({by_reviews})",
        f"- Brier score of P(top 5): **{cal.brier:.3f}**, against {cal.brier_base_rate:.3f} "
        "for always stating the base rate (lower is better)",
        f"- Projects ranked on the wrong side of the top-5 line: {cal.line_errors}; "
        f"flagged as a close call beforehand: **{cal.line_errors_flagged} "
        f"({cal.line_errors_flagged / cal.line_errors * 100:.0f}%)**",
        "",
        "| Stated P(top 5) | Projects | Mean stated | Really in the top 5 |",
        "|---|---|---|---|",
    ]
    for b in cal.bins:
        lines.append(
            f"| {b.low:.2f}–{b.high:.2f} | {b.count} | {b.stated:.2f} | {b.observed:.2f} |"
        )
    return "\n".join(lines)


# Charts sit on their own card, and switch palette with the reader's colour scheme,
# so they stay legible on a light or a dark GitHub page.
_CHART_STYLE = (
    "<style>.card{fill:#ffffff;stroke:#d0d7de}"
    "text{font:14px -apple-system,'Segoe UI',Helvetica,Arial,sans-serif;fill:#1f2328}"
    ".muted{fill:#59636e}.bar-raw{fill:#9aa5b1}.bar-shrunk_z{fill:#5b8def}"
    ".bar-additive{fill:#1f883d}.up{stroke:#1f883d}.down{stroke:#cf222e}.same{stroke:#9aa5b1}"
    "@media (prefers-color-scheme:dark){.card{fill:#0d1117;stroke:#30363d}text{fill:#e6edf3}"
    ".muted{fill:#9198a1}.bar-additive{fill:#3fb950}.up{stroke:#3fb950}.down{stroke:#f85149}"
    ".same{stroke:#6e7781}}</style>"
)


def _card(width: int, height: int) -> str:
    return f'<rect class="card" x="0.5" y="0.5" width="{width - 1}" height="{height - 1}" rx="10"/>'


def bar_chart_svg(stats: dict[str, dict[str, list[float]]], path: Path) -> None:
    methods = [("raw", "Raw mean"), ("shrunk_z", "Shrunken z"), ("additive", "Additive model")]
    width, height, left, bar_h, gap = 600, 200, 166, 30, 18
    rows = []
    for i, (key, label) in enumerate(methods):
        value = float(np.mean(stats[key]["rho"]))
        y = 52 + i * (bar_h + gap)
        w = (width - left - 76) * value
        rows.append(
            f'<text x="{left - 12}" y="{y + 20}" text-anchor="end">{label}</text>'
            f'<rect x="{left}" y="{y}" width="{w:.1f}" height="{bar_h}" rx="3" class="bar-{key}"/>'
            f'<text x="{left + w + 8:.1f}" y="{y + 20}">{value:.3f}</text>'
        )
    svg = (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}" role="img" '
        f'aria-labelledby="t"><title id="t">Mean Spearman correlation with the true order over '
        f"{RUNS} simulated events</title>"
        + _CHART_STYLE
        + _card(width, height)
        + f'<text x="{left}" y="32">Mean Spearman ρ with the truth '
        f'<tspan class="muted">({RUNS} runs; higher is better)</tspan></text>'
        + "".join(rows)
        + "</svg>\n"
    )
    path.write_text(svg, encoding="utf-8")


def rank_movement_svg(path: Path) -> None:
    result = evaluate(fixture_observations(), bootstrap=0)
    top = [p for p in result.projects if p.ranks["raw"] <= 10 or p.ranks["additive"] <= 10]
    width, height, x1, x2, top_y, step = 480, 470, 150, 330, 62, 38
    parts = []
    for p in top:
        y1 = top_y + (p.ranks["raw"] - 1) * step
        y2 = top_y + (p.ranks["additive"] - 1) * step
        move = (
            "up"
            if p.ranks["additive"] < p.ranks["raw"]
            else ("down" if p.ranks["additive"] > p.ranks["raw"] else "same")
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
            f'<line x1="{x1}" y1="{y1c}" x2="{x2}" y2="{y2c}" class="{move}" stroke-width="2.5"/>'
        )
    legend_y = height - 22
    svg = (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}" role="img" '
        'aria-labelledby="t2"><title id="t2">Rank movement on the fixtures: raw mean to the '
        "additive judge-bias model</title>"
        + _CHART_STYLE
        + _card(width, height)
        + f'<text x="{x1}" y="36" text-anchor="end">Raw mean</text>'
        f'<text x="{x2}" y="36">Bias-corrected</text>'
        + "".join(parts)
        + f'<line x1="40" y1="{legend_y - 5}" x2="64" y2="{legend_y - 5}" class="up" stroke-width="2.5"/>'
        f'<text x="70" y="{legend_y}" class="muted">moves up</text>'
        f'<line x1="160" y1="{legend_y - 5}" x2="184" y2="{legend_y - 5}" class="down" stroke-width="2.5"/>'
        f'<text x="190" y="{legend_y}" class="muted">moves down</text>' + "</svg>\n"
    )
    path.write_text(svg, encoding="utf-8")


def blocks(stats: dict[str, dict[str, list[float]]]) -> dict[str, str]:
    return {
        "fixture-results": fixture_section(),
        "simulation": simulation_section(stats),
        "calibration": calibration_section(calibrate(RUNS)),
        "sensitivity": sensitivity_section(),
        "kingmakers": kingmaker_section(),
    }


def apply(text: str, generated: dict[str, str]) -> str:
    for name, body in generated.items():
        pattern = re.compile(
            rf"(<!-- generated:{name} -->\n).*?(\n<!-- /generated:{name} -->)", re.DOTALL
        )
        if not pattern.search(text):
            raise SystemExit(f"JUDGING.md has no generated:{name} markers")
        text = pattern.sub(lambda m, b=body: m.group(1) + b + m.group(2), text)
    return text


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--check", action="store_true", help="Exit 1 if JUDGING.md is stale.")
    args = parser.parse_args(argv)
    stats = run_simulation()
    doc = ROOT / "JUDGING.md"
    current = doc.read_text(encoding="utf-8")
    updated = apply(current, blocks(stats))
    if args.check:
        if updated != current:
            print("JUDGING.md is out of date: run python tools/simulate_proof.py")
            return 1
        print("JUDGING.md numbers match the engine")
        return 0
    img = ROOT / "docs" / "img"
    img.mkdir(parents=True, exist_ok=True)
    bar_chart_svg(stats, img / "normalization-proof.svg")
    rank_movement_svg(img / "rank-movement.svg")
    doc.write_text(updated, encoding="utf-8", newline="\n")
    print("JUDGING.md and docs/img regenerated")
    return 0


if __name__ == "__main__":
    sys.exit(main())
