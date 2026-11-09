"""The factory sim's Head of Engineering check: it runs on its schedule, and the stuck rule's wake is not a notice."""

from types import SimpleNamespace

from evals.factory import scenarios


def run_with(*turns):
    bot = SimpleNamespace(turns=[{"action": None, **t} for t in turns])
    return SimpleNamespace(t=SimpleNamespace(bot=lambda slug: bot))


def test_the_stuck_rule_waking_the_manager_is_not_a_routine_notice():
    ok, _ = scenarios.lead_only_scheduled(run_with({"news": "blocker", "wake": "stuck_cards"}))
    assert ok


def test_a_blocker_notice_still_counts_against_the_schedule():
    ok, detail = scenarios.lead_only_scheduled(run_with({"news": "blocker", "wake": None}))
    assert not ok and "blocker 1" in detail
