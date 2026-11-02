"""Version skew: the runner built from origin/main (before native threads) against this server.

The rollout updates the server first, so a computer may run an older `tico-runner` for a while. These are the threads
turn tests and two conformance chat and task turns, run unchanged with the old binary in place of the release build.
The binary path comes from OLD_RUNNER_BINARY (built from origin/main's runner-rs by the check, never from this tree);
without it the module is skipped. Only the claude and codex harnesses run: they are the threads suite's own.

An old runner resumes one provider session per bot, so a turn in another thread or a direct message's text could continue
into a public thread (run on RunsOn, origin/main 672057c92, 2026-11-01). The server now sends an old runner's chat as
session `task`, so each chat keeps its own provider session (backend/execution.py start_claimed), and the privacy
assertions here must pass on it too.
"""

import json
import os
from pathlib import Path

import pytest

from runner.tests.test_threads_e2e_turns import *  # noqa: F401,F403  (the threads turn tests)
from backend.tests.threads_e2e import call, create_thread, ok, require_threads_api, say
from runner.tests.test_conformance import (  # noqa: F401
    test_a_chat_turn_streams_completes_and_resumes_its_own_thread_only,
    test_a_task_turn_works_in_its_own_worktree_with_the_tasks_repo_and_pushes_its_run,
)

# An old runner gives a fresh thread no history (runner-rs prompt.rs: a session with no saved cursor is told nothing but
# where the history is), so the replay half of each test cannot pass on it. Its privacy half stays a hard failure in the
# test it came from. An updated runner replays, which the new runner's suite checks.
REPLAY_LIMIT = "an old runner gives a fresh thread no history; recovery needs an updated runner"
test_the_fresh_root_thread_is_replayed_its_roots_earlier_messages = pytest.mark.xfail(
    strict=True, reason=REPLAY_LIMIT)(test_the_fresh_root_thread_is_replayed_its_roots_earlier_messages)
test_a_public_turn_is_replayed_its_threads_public_history = pytest.mark.xfail(
    strict=True, reason=REPLAY_LIMIT)(test_a_public_turn_is_replayed_its_threads_public_history)


@pytest.mark.parametrize("harness", ["claude"])
def test_a_sub_thread_turn_on_an_old_runner_never_reads_a_direct_message_or_another_threads_session(api, computer):
    """A sub-thread turn may lose its root's context on an old runner, but it must not read a direct message's text or
    resume the session that direct message's turn made."""
    require_threads_api(api)
    computer.start()
    dm = ok(call(api, "POST", "threads", "ana-test", {"audience": "personal", "members": ["bot:ops"]}))
    say(api, "ana-test", dm["id"], "@ops DM-SECRET: the board deck is late")
    until(lambda: finished(computer) == 1, "the reply in the direct message")
    dm_thread = provider_turns(computer)[-1][1]
    seen = len(computer.calls())
    t = create_thread(api, title="Launch", members=["bot:ops"])
    kickoff = say(api, "ana-test", t["id"], "Kickoff notes")
    say(api, "ana-test", t["id"], "@ops list the risks", parent_message_id=kickoff["id"])
    until(lambda: finished(computer) == 2, "the reply in the sub-thread")
    _kind, thread = provider_turns(computer)[-1]
    leaked = [call_ for call_ in computer.calls()[seen:] if "DM-SECRET" in json.dumps(call_)]
    assert not leaked and thread != dm_thread, \
        f"a sub-thread turn read the direct message or its session: leaked={leaked} thread={thread} dm={dm_thread}"


pytestmark = [pytest.mark.slow, pytest.mark.skipif(not os.environ.get("OLD_RUNNER_BINARY"),
                                                   reason="OLD_RUNNER_BINARY names the origin/main build")]


@pytest.fixture(scope="session")
def rust_binary():
    return Path(os.environ["OLD_RUNNER_BINARY"])


@pytest.fixture(params=["claude", "codex"])
def harness(request):
    return request.param
