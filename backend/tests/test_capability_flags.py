"""Runner capability flags are read from the runner's capabilities JSON, each time they are asked: the claim parses a
runner once per pass and passes the set down (execution.capability_set), so no parse is cached."""

from backend import execution


def test_capability_set_reads_the_runner_row():
    assert execution.capability_set('["fork_session_v1","session_roots_v1"]') == frozenset(
        {"fork_session_v1", "session_roots_v1"}
    )


def test_capability_set_is_not_cached():
    assert not hasattr(execution.capability_set, "cache_info")
    assert execution.capability_set(None) == frozenset()
    assert execution.capability_set("not json") == frozenset()
