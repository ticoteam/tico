"""Version skew: the runner built from origin/main (before native threads) against this server.

The rollout updates the server first, so a computer may run an older `tico-runner` for a while. These are the threads
turn tests and two conformance chat and task turns, run unchanged with the old binary in place of the release build.
The binary path comes from OLD_RUNNER_BINARY (built from origin/main's runner-rs by the check, never from this tree);
without it the module is skipped. Only the claude and codex harnesses run: they are the threads suite's own.
"""

import os
from pathlib import Path

import pytest

from runner.tests.test_threads_e2e_turns import *  # noqa: F401,F403  (the threads turn tests)
from runner.tests.test_conformance import (  # noqa: F401
    test_a_chat_turn_streams_completes_and_resumes_its_own_thread_only,
    test_a_task_turn_works_in_its_own_worktree_with_the_tasks_repo_and_pushes_its_run,
)

pytestmark = [pytest.mark.slow, pytest.mark.skipif(not os.environ.get("OLD_RUNNER_BINARY"),
                                                   reason="OLD_RUNNER_BINARY names the origin/main build")]


@pytest.fixture(scope="session")
def rust_binary():
    return Path(os.environ["OLD_RUNNER_BINARY"])


@pytest.fixture(params=["claude", "codex"])
def harness(request):
    return request.param
