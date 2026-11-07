"""Throwaway probe for the merge queue fail-fast path: passes on a pull request, fails in a queue. Never merged."""

import os


def test_probe_is_not_running_in_a_merge_queue_group() -> None:
    assert os.environ.get("GITHUB_EVENT_NAME") != "merge_group"
