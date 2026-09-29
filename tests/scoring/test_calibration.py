from scoring_engine.calibration import calibrate


def test_calibration_reports_consistent_numbers() -> None:
    cal = calibrate(3, bootstrap=40)
    assert cal.runs == 3 and cal.projects > 100
    assert 0.0 <= cal.coverage <= 1.0
    assert sum(b.count for b in cal.bins) == cal.projects
    for b in cal.bins:
        assert b.low <= b.stated <= b.high
        assert 0.0 <= b.observed <= 1.0
    assert 0 <= cal.line_errors_flagged <= cal.line_errors
    # Stated chances must beat always saying the base rate.
    assert cal.brier < cal.brier_base_rate


def test_calibration_is_deterministic() -> None:
    assert calibrate(2, bootstrap=30) == calibrate(2, bootstrap=30)
