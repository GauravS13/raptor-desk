"""Guard the acceptance-checker config against the traps found by reading run.py."""

import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

import run  # noqa: E402  (the official checker, unmodified)


def _raw() -> str:
    return (ROOT / ".dogfood.toml").read_text(encoding="utf-8")


def test_python39_fallback_parser_reads_the_same_values() -> None:
    """Python < 3.11 has no tomllib; run.py's fallback parser cuts lines at '#'."""
    assert run.parse_toml(_raw()) == tomllib.loads(_raw())


def test_no_value_contains_a_hash() -> None:
    config = tomllib.loads(_raw())
    for section in config.values():
        for value in section.values():
            values = value if isinstance(value, list) else [value]
            assert all("#" not in str(item) for item in values)


def test_config_has_every_key_the_checker_reads() -> None:
    config = tomllib.loads(_raw())
    assert config["portal"]["base_url"] == "http://localhost:8080"
    assert set(config["auth"]) == {"organizer", "judge_a", "judge_b", "participant"}
    assert set(config["routes"]) == {
        "gallery",
        "submit",
        "judge_scores",
        "peer_scores",
        "csv_export",
    }
    assert set(config["tiers"]["claimed"]) <= {"T1", "T2", "T3", "T4"}


def test_official_checker_is_unmodified() -> None:
    import hashlib

    digest = hashlib.sha256((ROOT / "tools" / "run.py").read_bytes()).hexdigest()
    assert digest == "aa98963841bc8e18e8e5d76f0499697c093dd3c0055f9d73a459f592f4dcf09d"
