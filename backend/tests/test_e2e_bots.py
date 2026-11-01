"""Bots end to end: a message becomes a job, a runner leases it, the real Rust runner (runner-rs/, built on the test run)
runs it through a fake harness, and the reply is visible in the conversation. A bot hands a message to another bot,
and a bot's approval is decided by its owner and consumed once.

Slow: the runner binary is built once per run (cargo), and the runner starts as a process. Run it on RunsOn:
`python3 scripts/remote.py --setup rust -- python3 -m pytest -q -o addopts='' -m slow backend/tests/test_e2e_bots.py`.
"""

import pytest

from backend.tests.test_api import api, assign, get, post, ready, setup_attempt  # noqa: F401
from backend.tests.live import block_outside_network, live  # noqa: F401
from runner.tests.test_conformance import Computer, reply_to, rust_binary, until  # noqa: F401

pytestmark = pytest.mark.slow


@pytest.fixture(autouse=True)
def no_outside_network(monkeypatch):
    """Only this computer is reached: a company made from a template calls no provider."""
    block_outside_network(monkeypatch, "the bot e2e test has no network")


@pytest.fixture
def fake_claude(api, live, tmp_path, rust_binary):
    """A computer on the fake `claude` harness: ops, with the runner, its logins and the readiness the server needs."""
    machine = Computer(api, live, tmp_path, "claude", rust_binary)
    yield machine
    machine.stop()


def job_for(api, message_id):
    with api.app.state.store.read() as c:
        row = c.execute("SELECT id, bot_id, state, attempt_id FROM jobs WHERE message_id=?", (message_id,)).fetchone()
    return dict(row) if row else None


def test_a_message_is_a_job_the_runner_leases_and_its_reply_is_visible(api, fake_claude):
    fake_claude.start()
    sent = post(api, "chat/ops", {"text": "Please answer locally"})
    answer = until(lambda: reply_to(api, sent["conversation_id"]), "the bot's reply")
    assert answer["body"] == "Acme reply."

    job = job_for(api, sent["id"])
    assert job["bot"] == "ops" and job["state"] == "completed" and job["attempt_id"]
    attempt = next(a for a in fake_claude.attempts() if a["id"] == job["attempt_id"])
    assert attempt["state"] == "completed" and attempt["result"]["outcome"] == "completed"
    assert {f"started:{attempt['id']}", f"complete:{attempt['id']}"} <= fake_claude.keys()
    assert "Please answer locally" in fake_claude.prompts()[-1]["prompt"]


def test_a_bot_hands_a_message_to_another_bot_that_needs_action_and_the_other_bot_takes_it_at_once(api):
    r, _, ops = setup_attempt(api)
    assign(api, r, "cpo")
    ready(api, r, ["ops", "cpo"])
    handoff = post(api, "messages", {"to": "cpo", "text": "QA update: the release candidate passed group 3. "
                                                          "Please write the release note.",
                                     "needs_action": True}, token=ops["token"])
    job = job_for(api, handoff["id"])
    assert job["bot"] == "cpo" and job["state"] == "queued"

    attempt = post(api, "jobs/claim", {"next_run": True, "bot": "cpo"}, token=r["token"])["attempt"]
    assert attempt["bot"] == "cpo" and attempt["message"]["id"] == handoff["id"]
    assert attempt["message"]["sender_id"] == "bot:ops"


def test_an_approval_a_bot_asks_for_is_decided_by_its_owner_and_consumed_once(api):
    _, _, bot = setup_attempt(api)
    payload = {"to": "colleague@acme.example", "cc": [], "subject": "Acme draft", "body_sha256": "a" * 64,
               "mailbox": "work"}
    approval = post(api, "approvals", {"kind": "send", "payload": payload}, token=bot["token"])
    path = "approvals/" + approval["id"]
    assert get(api, path, token="ana-test")["decision"] in (None, "")

    post(api, path, {"decision": "approved"}, token="ben-test", expected=403)      # not the decider
    post(api, path, {"decision": "approved"})
    assert get(api, path, token="ana-test")["decision"] == "approved"

    result = post(api, path + "/consume", {"payload_hash": approval["payload_hash"]}, token=bot["token"], key="send-1")
    assert result["consumed_at"]
    again = post(api, path + "/consume", {"payload_hash": approval["payload_hash"]}, token=bot["token"], key="send-1")
    assert again == result                                                          # a repeat is a replay
    post(api, path + "/consume", {"payload_hash": approval["payload_hash"]}, token=bot["token"], expected=422)
