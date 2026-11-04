"""Runner capability flags are read from the runner's capabilities JSON, each time they are asked: the claim parses a
runner once per pass and passes the set down (execution.capability_set), so no parse is cached."""

from backend import execution


def test_capability_flags_read_the_runner_row():
    runner = {"capabilities": '["task_lanes_v2","session_roots_v1"]'}
    assert execution.lanes_on(runner) is True
    assert execution.roots_on(runner) is True
    assert execution.task_goals_on(runner) is False
    assert execution.capability_set(runner["capabilities"]) == frozenset({"task_lanes_v2", "session_roots_v1"})


def test_capability_set_is_not_cached():
    assert not hasattr(execution.capability_set, "cache_info")
    assert execution.capability_set(None) == frozenset()
    assert execution.capability_set("not json") == frozenset()
