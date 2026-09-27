import importlib.util
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("truth_table", ROOT / "tools" / "truth_table.py")
truth_table = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(truth_table)

REPORT = """DOGFOOD 2026 acceptance report
T1  gallery is public ................. PASS
T2  judge cannot see peer scores ...... FAIL
T3  voting is anonymous ............... PASS

claimed T1 T2, verified T1
"""


def test_reports_are_parsed_into_checks_and_the_claim_line() -> None:
    checks, claim = truth_table.parse_report(REPORT, "official")
    assert [(c.tier, c.name, c.result) for c in checks] == [
        ("T1", "gallery is public", "PASS"),
        ("T2", "judge cannot see peer scores", "FAIL"),
        ("T3", "voting is anonymous", "PASS"),
    ]
    assert claim == "claimed T1 T2, verified T1"


def test_status_never_flatters() -> None:
    checks, _ = truth_table.parse_report(REPORT, "official")
    claimed = {"T1", "T2", "T4"}
    assert truth_table.status("T1", claimed, checks) == "**verified**"
    assert truth_table.status("T2", claimed, checks) == "**claimed, FAILING**"
    assert truth_table.status("T3", claimed, checks).startswith("started, not claimed")
    assert truth_table.status("T4", claimed, checks) == "**claimed, not yet checked**"


def test_a_modified_official_file_is_called_out(tmp_path: Path) -> None:
    for name in (".dogfood.toml", "acceptance-report.txt", "data/fixtures.json", "tools/run.py"):
        (tmp_path / name).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(ROOT / name, tmp_path / name)
    assert "DIFFERS" not in truth_table.render(tmp_path)
    with (tmp_path / "tools" / "run.py").open("a", encoding="utf-8") as handle:
        handle.write("\n# changed\n")
    assert "**DIFFERS from the published file**" in truth_table.render(tmp_path)


def test_readme_matches_the_committed_reports() -> None:
    assert truth_table.main(["--check"]) == 0
