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

from runner_support.tests.test_threads_e2e_turns import (  # noqa: F401
    computer,
    finished,
    live,
    provider_turns,
    setup,
    until,
    test_a_bot_woken_in_a_sub_thread_answers_there_from_that_subtree_and_no_sibling,
    test_a_mention_wakes_the_bot_and_plain_chatter_in_its_thread_does_not,
    test_the_loop_guard_holds_a_bots_wakes_and_a_persons_message_resumes_them,
    test_a_thread_keeps_its_provider_session_and_no_other_thread_shares_it,
    test_the_first_turn_after_an_update_starts_fresh_and_recovers_only_its_roots_history,
    test_a_public_turn_never_carries_a_direct_message_or_legacy_text,
)
from backend.tests.threads_e2e import call, create_thread, ok, require_threads_api, say
from runner_support.tests.test_conformance import (  # noqa: F401
    test_a_chat_turn_streams_completes_and_resumes_its_own_thread_only,
    test_a_task_turn_works_in_its_own_worktree_with_the_tasks_repo_and_pushes_its_run,
)


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
    assert not leaked and thread != dm_thread, (
        f"a sub-thread turn read the direct message or its session: leaked={leaked} thread={thread} dm={dm_thread}"
    )


pytestmark = [
    pytest.mark.slow,
    pytest.mark.skipif(not os.environ.get("OLD_RUNNER_BINARY"), reason="OLD_RUNNER_BINARY names the origin/main build"),
]


@pytest.fixture(scope="session")
def rust_binary():
    return Path(os.environ["OLD_RUNNER_BINARY"])


@pytest.fixture(params=["claude", "codex"])
def harness(request):
    return request.param
