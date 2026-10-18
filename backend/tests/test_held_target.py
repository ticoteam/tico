"""Writes to a paused or quarantined bot: kept and said so, or refused without counting against the sender."""
import pytest

from backend.tests.test_api import api, api_settings, assign, claim, post, ready, runner, setup_attempt  # noqa: F401  (fixtures)
from backend.tests.test_member_bots import finish
from backend.store import H


def revision(api, bot):
    with api.app.state.store.read() as c:
        return c.execute("SELECT revision FROM bot_config WHERE bot=?", (bot,)).fetchone()[0]


@pytest.mark.parametrize("held", ["quarantined", "paused"])
def test_a_bots_message_and_comment_to_a_held_bot_wait_and_run_once_it_is_back(api, held):
    _, _, attempt = setup_attempt(api)                      # ops, the sender, in a run
    machine = runner(api, label="Finance Mac")
    assign(api, machine, "finance")
    ready(api, machine, ["finance"])
    task = post(api, "tasks", {"owner": "finance", "title": "Check the invoice run", "body": "Read the fixture invoices"},
                token=attempt["token"])
    while (work := claim(api, machine, "finance")):            # the new-task notice: done before it is held
        finish(api, machine, work)
    if held == "quarantined":
        with api.app.state.store.transaction() as c:
            H.quarantine(c, "finance", "10 repeated refusals today")
    else:
        post(api, "bots/finance/control", {"action": "pause", "expected_revision": revision(api, "finance")})

    sent = post(api, "messages", {"to": "finance", "text": "Review 1 is ready"}, token=attempt["token"])
    comment = post(api, f"tasks/{task['id']}/comments", {"text": "Finding 1"}, token=attempt["token"])
    assert "is kept" in sent["warning"] and "is kept" in comment["warning"], (sent, comment)
    assert ("until about" if held == "quarantined" else "until a person resumes it") in sent["warning"]
    refused = post(api, "tasks", {"owner": "finance", "title": "Check the payroll run", "body": "Read it"},
                   token=attempt["token"], expected=422)
    assert refused["error"]["code"] == "target_inactive" and "is not a repeat" in refused["error"]["detail"]
    kept = {sent["id"], comment["comment"]["id"]}
    with api.app.state.store.read() as c:
        assert H.bot(c, "ops")["state"] == "active"
        jobs = c.execute(f"SELECT message_id, state FROM jobs WHERE message_id IN ({','.join('?' * len(kept))})",
                         tuple(kept)).fetchall()
    assert sorted(j["state"] for j in jobs) == ["queued", "queued"], "kept once each, waiting"
    assert claim(api, machine, "finance") is None, "nothing runs while it is held"

    if held == "quarantined":
        post(api, "bots/finance/quarantine/clear", {})
    else:
        post(api, "bots/finance/control", {"action": "resume", "expected_revision": revision(api, "finance")})
    ran = set()
    while (work := claim(api, machine, "finance")):
        with api.app.state.store.read() as c:
            ran |= {r["message_id"] for r in c.execute("SELECT message_id FROM jobs WHERE attempt_id=?", (work["id"],))}
        finish(api, machine, work)
    with api.app.state.store.read() as c:
        states = [r["state"] for r in c.execute(f"SELECT state FROM jobs WHERE message_id IN ({','.join('?' * len(kept))})",
                                                tuple(kept))]
    assert ran == kept and states == ["completed", "completed"], "both run once it is back, with no resend"
