"""Version skew: the runner built from origin/main (before native threads) against this server.

The rollout updates the server first, so a computer may run an older `tico-runner` for a while. These are the threads
turn tests and the conformance task turn, run unchanged with the old binary in place of the release build, and the
conformance chat turn as an old runner can meet it (it keeps no process per thread, so only its privacy checks hold).
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
from backend.tests.helpers.api import post
from runner_support.tests.helpers.conformance import reply_to
from runner_support.tests.test_conformance import (  # noqa: F401
    session_named,
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


def test_an_old_runners_chat_turn_resumes_its_own_thread_only(api, computer):
    """The conformance chat test, as an old runner can meet it. An old runner keeps no process per thread: it resumes the
    thread's provider session in a new process (claude --resume, codex thread/resume). What must hold across the skew is
    privacy: the same thread's follow-up continues its own session, and another person's thread never resumes it."""
    computer.start()
    first = post(api, "chat/ops", {"text": "Please answer locally"})
    until(lambda: reply_to(api, first["conversation_id"]), "the bot's reply")
    same = post(api, "chat/ops", {"text": "And one more thing"})
    assert same["conversation_id"] == first["conversation_id"]
    until(lambda: len([a for a in computer.attempts() if a["result"]]) == 2, "the follow-up's reply")
    other = post(api, "chat/ops", {"text": "A separate question from Ben"}, token="ben-test")
    assert other["conversation_id"] != first["conversation_id"]
    until(lambda: reply_to(api, other["conversation_id"], "ben-test"), "the other thread's reply")
    if computer.harness == "claude":
        calls = computer.prompts()
        session = calls[0]["argv"][calls[0]["argv"].index("--session-id") + 1]
        assert len(calls) == 3 and session_named(calls[1]) == session
        assert session not in calls[-1]["argv"] and "--resume" not in calls[-1]["argv"]
    else:
        calls = [c for c in computer.calls() if c["harness"] == computer.harness]
        starts = [c for c in calls if c.get("method") in ("thread/start", "thread/resume")]
        turns = [c for c in calls if c.get("method") == "turn/start"]
        assert len(turns) == 3 and turns[1]["thread"] == turns[0]["thread"] != turns[2]["thread"]
        assert starts[-1]["method"] == "thread/start", "another person's thread resumed an existing provider thread"
    assert "Please answer locally" not in computer.prompts()[-1]["prompt"]


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
