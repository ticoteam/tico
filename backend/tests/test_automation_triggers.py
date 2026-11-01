"""What starts an automation (backend/automations.py): a task received, a catalog event, mail, Slack, an incoming
webhook, listening and a schedule (DST-safe), with trigger filters, batch times, and the chain guard."""
import json
from datetime import datetime, timezone
from types import SimpleNamespace

import time_machine

from backend import automations
from backend.tests.test_api import api, get, headers, post  # noqa: F401

WAKE = {"id": "tell", "type": "action", "do": "wake_bot", "args": {"bot": "ops", "text": "{{task.title}}"}}


def make(api, trigger, steps=(WAKE,), **extra):
    return post(api, "automations", {"name": "Test", "owner": "person:ana", "trigger": trigger, "steps": list(steps),
                                     **extra})["id"]


def runs(api, aid):
    return get(api, f"automations/{aid}/runs")


def fire(api, fn, *args):
    with api.app.state.store.transaction() as c:
        queued = fn(c, *args)
    api.app.state.store.automations.drain()
    return queued


def mail(msg_id="m1", subject="Invoice 4471", sender="billing@acme.example"):
    return SimpleNamespace(msg_id=msg_id, thread_id="t1", from_addr=sender, to=["ana@acme.example"], subject=subject,
                           snippet="Please pay", body="Please pay the invoice.", labels=["INBOX"], list_id=None,
                           is_internal=False, date="2026-10-28T09:00:00Z")


# ---------------------------------------------------------------------------------------------- tasks and events
def test_a_task_received_runs_its_owners_automation_only(api):
    aid = make(api, {"type": "task_received", "to": "bot:ops"})
    post(api, "tasks", {"owner": "ops", "title": "Check the deploy", "body": "Please."})
    post(api, "tasks", {"owner": "coo", "title": "Not for ops", "body": "Please."})
    done = runs(api, aid)["runs"]
    assert len(done) == 1 and done[0]["status"] == "ok" and done[0]["trigger_ref"].startswith("task:")


def test_trigger_filters_drop_a_run_and_count_it_for_the_day(api):
    aid = make(api, {"type": "task_received", "to": "bot:ops",
                     "filters": [{"field": "task.title", "op": "contains", "value": "urgent"}]})
    post(api, "tasks", {"owner": "ops", "title": "Routine check", "body": "Please."})
    seen = runs(api, aid)
    assert seen["runs"] == [] and seen["daily"][0]["dropped"] == 1 and seen["daily"][0]["runs"] == 0
    post(api, "tasks", {"owner": "ops", "title": "An urgent check", "body": "Please."})
    assert len(runs(api, aid)["runs"]) == 1


def test_an_event_trigger_and_the_chain_guard(api):
    spawn = {"id": "spawn", "type": "action", "do": "create_task", "args": {"title": "Follow up", "owner": "coo"}}
    aid = make(api, {"type": "event", "name": "task.created"}, [spawn])
    post(api, "tasks", {"owner": "ops", "title": "Start", "body": "Please."})
    # Its own task.created never starts it again: one run, one follow-up.
    assert len(runs(api, aid)["runs"]) == 1
    with api.app.state.store.read() as c:
        assert c.execute("SELECT count(*) FROM tasks WHERE title='Follow up'").fetchone()[0] == 1


def test_a_paused_automation_starts_nothing(api):
    aid = make(api, {"type": "task_received", "to": "bot:ops"}, enabled=False)
    post(api, "tasks", {"owner": "ops", "title": "Paused", "body": "Please."})
    assert runs(api, aid)["runs"] == []


# ---------------------------------------------------------------------------------------------- mail and the send switch
def test_mail_runs_the_inboxs_automation_and_queues_mail_actions_for_its_computer(api):
    archive = {"id": "gate", "type": "filter", "rules": [
        {"when": [{"field": "message.subject", "op": "contains", "value": "invoice"}], "then": [{"do": "archive"}]}]}
    aid = make(api, {"type": "message", "inbox": "ana@acme.example"}, [archive])
    assert len(fire(api, automations.on_mail, "ANA@acme.example", mail())) == 1
    assert fire(api, automations.on_mail, "ben@acme.example", mail("m2")) == []
    assert runs(api, aid)["runs"][0]["status"] == "waiting"       # until the mail computer reports the archive
    with api.app.state.store.read() as c:
        queued = [dict(r) for r in c.execute("SELECT mailbox, msg_id, action, state FROM automation_mail_actions")]
    assert queued == [{"mailbox": "ana@acme.example", "msg_id": "m1", "action": "archive", "state": "queued"}]


def test_send_reply_follows_the_inbox_send_switch(api):
    reply = {"id": "reply", "type": "action", "do": "send_reply", "args": {"body": "Thanks"}}
    make(api, {"type": "message", "inbox": "ana@acme.example"}, [reply])
    assert get(api, "inboxes/ana@acme.example") == {"inbox": "ana@acme.example", "send": True}
    fire(api, automations.on_mail, "ana@acme.example", mail("m1"))
    r = api.patch("/api/v2/inboxes/ana@acme.example", json={"send": False}, headers=headers())
    assert r.status_code == 200 and r.json() == {"inbox": "ana@acme.example", "send": False}
    fire(api, automations.on_mail, "ana@acme.example", mail("m2"))
    with api.app.state.store.read() as c:
        actions = [(r["msg_id"], r["action"]) for r in c.execute(
            "SELECT msg_id, action FROM automation_mail_actions ORDER BY created_at")]
        off = c.execute("SELECT count(*) FROM events WHERE action='automation.send_off'").fetchone()[0]
    assert actions == [("m1", "send"), ("m2", "draft")] and off == 1


def test_only_the_inboxs_person_or_the_top_sets_its_switch(api):
    assert api.patch("/api/v2/inboxes/ana@acme.example", json={"send": False},
                     headers=headers("ben-test")).status_code == 403
    r = api.patch("/api/v2/inboxes/ben@acme.example", json={"send": False}, headers=headers("ben-test"))
    assert r.status_code == 200
    from backend import audit_log
    with api.app.state.store.read() as c:
        row = audit_log.rows(c, obj="inbox:ben@acme.example")["rows"][0]
    assert row["actor"] == "human:ben" and row["before"]["send"] is True and row["after"]["send"] is False


# ---------------------------------------------------------------------------------------------- Slack, webhooks, listening
def listed(api, name, readers=()):
    """A channel on the team's Slack list (Settings > Tools > Slack)."""
    with api.app.state.store.transaction() as c:
        c.execute("INSERT INTO slack_channels(key,name,readers,created_at,updated_at) VALUES(?,?,?,?,?)",
                  ("#" + name, name, json.dumps(list(readers)), "2026-10-28T00:00:00Z", "2026-10-28T00:00:00Z"))


def test_slack_messages_reach_the_dm_or_channel_inbox(api):
    listed(api, "general")
    dm = make(api, {"type": "message", "inbox": "slack:dm"})
    general = make(api, {"type": "message", "inbox": "slack:#general"})
    person = {"id": "ana"}
    fire(api, automations.on_slack, {"channel": "D1", "channel_kind": "im", "ts": "1.1", "text": "hi"}, person,
         {"kind": "im", "name": "DM"})
    fire(api, automations.on_slack, {"channel": "C1", "channel_kind": "channel", "ts": "1.2", "text": "hi"}, person,
         {"kind": "channel", "name": "#General"})
    assert not fire(api, automations.on_slack, {"channel": "C2", "channel_kind": "channel", "ts": "1.3"}, person,
                    {"kind": "channel", "name": "#random"})
    assert len(runs(api, dm)["runs"]) == 1 and len(runs(api, general)["runs"]) == 1


def test_a_webhook_and_a_listening_post_start_their_automations(api):
    hook = make(api, {"type": "webhook", "id": "trg_1"})
    leads = make(api, {"type": "listening", "destination": "leads"})
    out = fire(api, automations.on_webhook, "trg_1", "Deploys", "custom", "d1", {"ok": True}, "")
    assert out[0]["outcome"] == "automation" and out[0]["ok"]
    assert fire(api, automations.on_webhook, "trg_2", "Other", "custom", "d2", {}, "") == []
    fire(api, automations.on_listening, {"id": "i1", "destination": "leads"},
         {"url": "https://acme.example/post", "content": "Looking for a tool"})
    assert len(runs(api, hook)["runs"]) == 1 and len(runs(api, leads)["runs"]) == 1


# ---------------------------------------------------------------------------------------------- schedules and batches
def test_a_schedule_keeps_its_wall_clock_time_across_daylight_saving(api):
    with time_machine.travel(datetime(2026, 10, 31, 13, 30, tzinfo=timezone.utc), tick=False):
        aid = make(api, {"type": "schedule", "cron": "0 9 * * *", "tz": "America/New_York"})
    store = api.app.state.store
    automations.tick(store, datetime(2026, 10, 31, 13, 30, tzinfo=timezone.utc))   # after Saturday's 09:00 EDT
    assert get(api, f"automations/{aid}")["next_run"] == "2026-11-01T14:00:00.000000Z"   # Sunday 09:00 EST
    automations.tick(store, datetime(2026, 11, 1, 13, 59, tzinfo=timezone.utc))
    assert runs(api, aid)["runs"] == []
    automations.tick(store, datetime(2026, 11, 1, 14, 0, 30, tzinfo=timezone.utc))
    done = runs(api, aid)["runs"]
    assert len(done) == 1 and done[0]["trigger_ref"] == "schedule:2026-11-01T14:00:00.000000Z"
    assert get(api, f"automations/{aid}")["next_run"] == "2026-11-02T14:00:00.000000Z"


def test_a_new_schedule_runs_at_the_next_run_it_showed_even_if_the_first_pass_comes_later(api):
    with time_machine.travel(datetime(2026, 10, 20, 9, 0, tzinfo=timezone.utc), tick=False):
        aid = make(api, {"type": "schedule", "cron": "0 7 * * *", "tz": "UTC"})
        assert get(api, f"automations/{aid}")["next_run"] == "2026-10-21T07:00:00.000000Z"
    automations.tick(api.app.state.store, datetime(2026, 10, 21, 7, 5, tzinfo=timezone.utc))   # no pass before
    done = runs(api, aid)["runs"]
    assert len(done) == 1 and done[0]["trigger_ref"] == "schedule:2026-10-21T07:00:00.000000Z"


def test_a_batched_inbox_filters_on_receipt_and_runs_the_rest_at_the_batch_time(api):
    gate = {"id": "gate", "type": "filter", "rules": [{"when": [], "then": [{"do": "label"}]}]}
    with time_machine.travel(datetime(2026, 10, 28, 9, 0, tzinfo=timezone.utc), tick=False):
        aid = make(api, {"type": "message", "inbox": "ana@acme.example"}, [gate, WAKE | {"args": {"bot": "ops"}}],
                   batch={"times": ["13:00"]})
        fire(api, automations.on_mail, "ana@acme.example", mail())
        held = runs(api, aid)["runs"][0]
        assert held["status"] == "waiting" and [s["id"] for s in held["steps"]] == ["gate"]
    with time_machine.travel(datetime(2026, 10, 28, 13, 0, 5, tzinfo=timezone.utc), tick=False):
        automations.tick(api.app.state.store)
        done = runs(api, aid)["runs"][0]
    # Every step ran; the run still waits for the mail computer to report the label.
    assert [(s["id"], s["status"]) for s in done["steps"]] == [("gate", "waiting"), ("tell", "ok")]
    assert done["status"] == "waiting"


# ---------------------------------------------------------------------------------------------- retention
def test_step_detail_is_pruned_after_30_days_and_the_run_row_stays(api):
    aid = make(api, {"type": "schedule", "cron": "0 9 * * *", "tz": "UTC"})
    run = post(api, f"automations/{aid}/run", {})["run"]
    store = api.app.state.store
    from backend.store import H
    with store.transaction() as c:
        automations.prune(c, H.shift(run["finished_at"], days=29))
    assert runs(api, aid)["runs"][0]["steps"]
    with store.transaction() as c:
        automations.prune(c, H.shift(run["finished_at"], days=31))
    kept = runs(api, aid)["runs"][0]
    assert kept["id"] == run["id"] and kept["status"] == "ok" and kept["steps"] is None
