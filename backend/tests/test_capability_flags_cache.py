"""Runner capability flags share one parsed frozenset per capabilities_json."""

from backend import execution


def test_capability_flags_reuse_parsed_set_for_same_capabilities_json():
    execution._capability_set.cache_clear()
    runner = {"capabilities": '["task_lanes_v2","session_roots_v1"]'}
    assert execution.lanes_on(runner) is True
    assert execution.roots_on(runner) is True
    assert execution.task_goals_on(runner) is False
    assert execution._capability_set.cache_info().hits >= 2
    assert execution._capability_set.cache_info().misses == 1
