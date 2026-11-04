"""Default projections from posted history: a trend never goes below zero, a run rate scales to the month's length,
and a line with no history has no default at all."""
from backend.finance.core import projections as PJ


def test_a_trend_never_projects_below_zero():
    assert PJ.estimate("trend", [300, 200, 100], 0, 31, 1) == 0
    assert PJ.estimate("trend", [100, 200, 300], 0, 31, 1) == 400


def test_a_run_rate_scales_the_last_30_days_to_the_month():
    assert PJ.estimate("run_rate", [0, 0], 300, 31, 1) == 310
    assert PJ.estimate("run_rate", [0, 0], 300, 28, 1) == 280


def test_no_posted_history_means_no_default():
    assert PJ.estimate("trend", [0, 0], 0, 31, 1) is None
    assert PJ.estimate("recurring", [0], 0, 30, 1) is None
