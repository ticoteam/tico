"""Writes to a paused or quarantined bot: kept and said so, or refused without counting against the sender."""
import dataclasses

import pytest

from backend import slack_gateway as G
from backend.store import Store
from backend.tests import test_slack_gateway as S
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

    sent = post(api, "messages", {"to": "finance", "text": "Review 1 is ready", "needs_action": True}, token=attempt["token"])
    comment = post(api, f"tasks/{task['id']}/comments", {"text": "Finding 1", "needs_action": True}, token=attempt["token"])
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


def run_all(api, machine, bot):
    """Claim and finish everything the runner is given for the bot; the message ids each run carried."""
    ran = []
    while (work := claim(api, machine, bot)):
        with api.app.state.store.read() as c:
            ran += [r["message_id"] for r in c.execute("SELECT message_id FROM jobs WHERE attempt_id=?", (work["id"],))]
        finish(api, machine, work)
    return ran


@pytest.mark.parametrize("held", ["quarantined", "paused"])
def test_a_persons_new_slack_thread_to_a_held_bot_waits_and_runs_once_it_is_back(api, held):
    """A person's new Slack DM thread to a bot held while the decision model reads the roster: kept, nothing claims
    it while held, and after a normal resume or quarantine clear it runs once on the runner, with no resend."""
    machine = runner(api, label="Finance Mac")
    assign(api, machine, "finance")
    ready(api, machine, ["finance"])
    store = Store(dataclasses.replace(api.app.state.store.settings, slack_team_id=S.TEAM, slack_app_id=S.APP))
    gateway = G.Gateway(store, S.FakeSlack(), judge_engine=S.FakeJudge())
    gateway.verify_app()

    def hold(state):
        if held == "quarantined":
            with api.app.state.store.transaction() as c:
                H.quarantine(c, "finance", "10 repeated refusals today")
        else:
            post(api, "bots/finance/control", {"action": "pause", "expected_revision": revision(api, "finance")})
        return S.answers(finance=0.9)

    gateway._judge.answers.append(hold)
    assert gateway.receive(S.envelope("please check the invoice run", user="U1", channel=S.DM, kind="message")) == "queued"
    (result,) = gateway.tick()["events"]
    (kept,) = result["decision"]["delivered"]
    assert kept["bot_id"] == "finance" and kept["opened"] and "is kept" in kept["held"], result
    assert [p["text"] for p in gateway.slack.posts] == [kept["held"]]
    assert claim(api, machine, "finance") is None, "nothing runs while it is held"

    if held == "quarantined":
        post(api, "bots/finance/quarantine/clear", {})
    else:
        post(api, "bots/finance/control", {"action": "resume", "expected_revision": revision(api, "finance")})
    assert run_all(api, machine, "finance") == [kept["message_id"]], "it runs once it is back"
    assert gateway.tick()["events"] == [] and run_all(api, machine, "finance") == [], "and is not sent again"
    with api.app.state.store.read() as c:
        jobs = [dict(r) for r in c.execute("SELECT bot_id, message_id, state FROM jobs")]
        sent = c.execute("SELECT count(*) FROM messages WHERE sender_id='human:ana' AND recipient_id='bot:finance'").fetchone()[0]
    assert jobs == [{"bot_id": "finance", "message_id": kept["message_id"], "state": "completed"}] and sent == 1
