"""What an automation queues for mail, before the server carries it out (backend/mail_actions.py,
test_mail_server_actions.py): a sample message names the trigger's inbox, and a Slack reply delivered after the
inbox's send switch went off is held as a draft task."""

import json
from pathlib import Path

from backend.tests.helpers.automation_mail_actions import automate


def test_redesign_promo_sample_queues_danas_archive_without_a_mailbox_row():
    from backend import automations, mailbox_access
    from evals.redesign import seeds
    from evals.redesign.harness import DANA_REF, Org

    # A sample message the connector can identify.
    sample = json.loads((Path(__file__).resolve().parent / "fixtures" / "redesign-automation-promo.json").read_text())
    with Org() as world:
        seeds.automations(world)
        listed = world.api("get", "/api/v2/automations")
        assert listed.status == 200, listed.body
        triage = next(a for a in listed.body["automations"] if a["name"] == "Inbox triage")
        inbox = triage["trigger"]["inbox"]
        with world.store.read() as c:
            assert triage["owner_id"] == DANA_REF
            assert mailbox_access.principal(c, DANA_REF) == mailbox_access.Principal("person", "dana")
            assert c.execute("SELECT 1 FROM mailboxes WHERE address=?", (inbox,)).fetchone() is None
            assert mailbox_access.level(c, DANA_REF, inbox) == "read_write"
            assert automations.may_read_inbox(c, DANA_REF, inbox)
            assert mailbox_access.action_refusal(c, DANA_REF, inbox, "archive") is None
        reply = world.api("post", f"/api/v2/automations/{triage['id']}/run", {"input": sample})
        assert reply.status == 200, reply.body
        run = reply.body["run"]
        with world.store.read() as c:
            (action,) = c.execute(
                "SELECT mailbox, msg_id, action FROM automation_mail_actions WHERE run_id=?", (run["id"],)
            ).fetchall()
            assert dict(action) == {"mailbox": inbox, "msg_id": "promo-sample", "action": "archive"}


def test_a_sample_naming_no_mailbox_is_mail_in_the_trigger_inbox():
    """The page's default sample has no mailbox; its archive targets the trigger's inbox, not none (refused)."""
    from evals.redesign import seeds
    from evals.redesign.harness import Org

    with Org() as world:
        seeds.automations(world)
        triage = next(
            a for a in world.api("get", "/api/v2/automations").body["automations"] if a["name"] == "Inbox triage"
        )
        sample = {"message": {"from": "promo@deals.example", "subject": "Sale", "body": "50% off"}}
        reply = world.api("post", f"/api/v2/automations/{triage['id']}/run", {"input": sample})
        assert reply.status == 200, reply.body
        with world.store.read() as c:
            (action,) = c.execute(
                "SELECT mailbox, action FROM automation_mail_actions WHERE run_id=?", (reply.body["run"]["id"],)
            ).fetchall()
        assert dict(action) == {"mailbox": triage["trigger"]["inbox"], "action": "archive"}


def test_a_slack_reply_delivered_after_the_switch_went_off_is_held_as_a_draft_task(api):
    from backend import automation_actions as A
    from backend import slack_gateway as G
    from backend.tests.helpers.slack_gateway import FakeSlack

    store = api.app.state.store
    slack_msg = {
        "message": {"source": "slack", "inbox": "slack:#general", "channel": "C1", "ts": "1.1", "text": "Help?"}
    }
    auto = {"id": automate(api, NOTE), "flow": {"owner": "person:ana"}, "assistant_bot": "assistant"}
    with store.transaction() as c:
        c.execute(
            "INSERT INTO automation_runs(id,automation_id,version,started_at,status,saved_state) "
            "VALUES('run_s',?,1,?,'ok',?)",
            (auto["id"], "2026-10-28T09:00:00Z", json.dumps({"status": "ok", "steps": []})),
        )
        done = A.run(c, auto, "run_s", "send_reply", {"body": "On it", "bot": "ops"}, slack_msg)
        c.execute(
            "UPDATE automation_runs SET saved_state=? WHERE id='run_s'",
            (
                json.dumps(
                    {
                        "status": "ok",
                        "threads": [],
                        "steps": [
                            {
                                "id": "reply",
                                "type": "action",
                                "status": "ok",
                                "outputs": {"do": "send_reply", **done},
                                "cost": 0,
                            }
                        ],
                    }
                ),
            ),
        )
        c.execute("INSERT INTO automation_inboxes VALUES('slack:#general',0,?,'human:ana')", ("2026-10-28T09:01:00Z",))
    slack = FakeSlack()
    G.Gateway(store, slack).deliver()
    assert slack.posts == []
    with store.read() as c:
        held = dict(
            c.execute("SELECT state, last_error AS error FROM outbound_actions WHERE channel='slack'").fetchone()
        )
        task = c.execute("SELECT owner_id, title FROM tasks WHERE title LIKE 'Draft reply:%'").fetchone()
        state = json.loads(c.execute("SELECT saved_state FROM automation_runs WHERE id='run_s'").fetchone()[0])
    assert held == {"state": "canceled", "error": "send_off"} and dict(task) == {
        "owner_id": "human:ana",
        "title": "Draft reply: On it",
    }
    assert state["steps"][0]["outputs"]["detail"] == "send_off"


NOTE = {"id": "tell", "type": "action", "do": "wake_bot", "args": {"bot": "ops", "text": "Look"}}
