"""What starts an automation (backend/automations.py): a task received, a catalog event, mail, Slack, an incoming
webhook, listening and a schedule (DST-safe), with trigger filters, batch times, and the chain guard."""

import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import time_machine

from backend import automations
from backend import hubdb as H
from backend.tests.helpers.api import assign, get, headers, post, ready, runner  # noqa: F401
from backend.tests.helpers.automation_turns import finish
from backend.tests.helpers.fake_gmail import fake_gmail  # noqa: F401

WAKE = {"id": "tell", "type": "action", "do": "wake_bot", "args": {"bot": "ops", "text": "{{task.title}}"}}


def make(api, trigger, steps=(WAKE,), **extra):
    return post(
        api, "automations", {"name": "Test", "owner": "person:ana", "trigger": trigger, "steps": list(steps), **extra}
    )["id"]


def runs(api, aid):
    return get(api, f"automations/{aid}/runs")


def fire(api, fn, *args):
    with api.app.state.store.transaction() as c:
        queued = fn(c, *args)
    api.app.state.store.automations.drain()
    return queued


def mail(msg_id="m1", subject="Invoice 4471", sender="billing@acme.example"):
    return SimpleNamespace(
        msg_id=msg_id,
        thread_id="t1",
        from_addr=sender,
        to=["ana@acme.example"],
        subject=subject,
        snippet="Please pay",
        body="Please pay the invoice.",
        labels=["INBOX"],
        list_id=None,
        is_internal=False,
        date="2026-10-28T09:00:00Z",
    )


# ---------------------------------------------------------------------------------------------- tasks and events
def test_a_task_received_runs_its_owners_automation_only(api):
    aid = make(api, {"type": "task_received", "to": "bot:ops"})
    post(api, "tasks", {"owner": "ops", "title": "Check the deploy", "body": "Please."})
    post(api, "tasks", {"owner": "assistant", "title": "Not for ops", "body": "Please."})
    done = runs(api, aid)["runs"]
    assert len(done) == 1 and done[0]["status"] == "ok" and done[0]["trigger_ref"].startswith("task:")


def test_a_task_received_reaches_its_automation_with_its_pipeline_and_stage(api):
    """A task filed with no pipeline is in General's first stage: a filter on task.pipeline_id and task.stage_id
    matches it."""
    aid = make(
        api,
        {
            "type": "task_received",
            "to": "bot:ops",
            "filters": [
                {"field": "task.pipeline_id", "op": "is", "value": "general"},
                {"field": "task.stage_id", "op": "is", "value": "general-todo"},
            ],
        },
    )
    post(api, "tasks", {"owner": "ops", "title": "Untyped check", "body": "Please."})
    assert len(runs(api, aid)["runs"]) == 1


def test_trigger_filters_drop_a_run_and_count_it_for_the_day(api):
    aid = make(
        api,
        {
            "type": "task_received",
            "to": "bot:ops",
            "filters": [{"field": "task.title", "op": "contains", "value": "urgent"}],
        },
    )
    post(api, "tasks", {"owner": "ops", "title": "Routine check", "body": "Please."})
    seen = runs(api, aid)
    assert seen["runs"] == [] and seen["daily"][0]["dropped"] == 1 and seen["daily"][0]["runs"] == 0
    post(api, "tasks", {"owner": "ops", "title": "An urgent check", "body": "Please."})
    assert len(runs(api, aid)["runs"]) == 1


def test_an_event_trigger_and_the_chain_guard(api):
    spawn = {"id": "spawn", "type": "action", "do": "create_task", "args": {"title": "Follow up", "owner": "assistant"}}
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
def test_mail_runs_the_inboxs_automation_and_the_server_carries_out_its_mail_actions(api, fake_gmail):
    archive = {
        "id": "gate",
        "type": "filter",
        "rules": [
            {"when": [{"field": "message.subject", "op": "contains", "value": "invoice"}], "then": [{"do": "archive"}]}
        ],
    }
    aid = make(api, {"type": "message", "inbox": "ana@acme.example"}, [archive])
    assert len(fire(api, automations.on_mail, "ANA@acme.example", mail())) == 1
    assert fire(api, automations.on_mail, "ben@acme.example", mail("m2")) == []
    assert runs(api, aid)["runs"][0]["status"] == "ok"  # the server archived it in Gmail
    with api.app.state.store.read() as c:
        queued = [dict(r) for r in c.execute("SELECT mailbox, msg_id, action, state FROM automation_mail_actions")]
    assert queued == [{"mailbox": "ana@acme.example", "msg_id": "m1", "action": "archive", "state": "done"}]


def test_send_reply_follows_the_inbox_send_switch(api):
    reply = {"id": "reply", "type": "action", "do": "send_reply", "args": {"body": "Thanks"}}
    make(api, {"type": "message", "inbox": "ana@acme.example"}, [reply])
    assert get(api, "inboxes/ana@acme.example") == {"inbox": "ana@acme.example", "send": True}
    fire(api, automations.on_mail, "ana@acme.example", mail("m1"))
    r = api.patch("/api/v2/inboxes/ana@acme.example", json={"send": False}, headers=headers())
    assert r.status_code == 200 and r.json() == {"inbox": "ana@acme.example", "send": False}
    fire(api, automations.on_mail, "ana@acme.example", mail("m2"))
    with api.app.state.store.read() as c:
        actions = [
            (r["msg_id"], r["action"])
            for r in c.execute("SELECT msg_id, action FROM automation_mail_actions ORDER BY created_at")
        ]
        off = c.execute("SELECT count(*) FROM events WHERE action='automation.send_off'").fetchone()[0]
    assert actions == [("m1", "send"), ("m2", "draft")] and off == 1


def test_only_the_inboxs_person_or_the_top_sets_its_switch(api):
    assert (
        api.patch("/api/v2/inboxes/ana@acme.example", json={"send": False}, headers=headers("ben-test")).status_code
        == 403
    )
    r = api.patch("/api/v2/inboxes/ben@acme.example", json={"send": False}, headers=headers("ben-test"))
    assert r.status_code == 200
    from backend import audit_log

    with api.app.state.store.read() as c:
        row = audit_log.rows(c, obj="inbox:ben@acme.example")["rows"][0]
    assert row["actor"] == "human:ben" and row["before"]["send"] is True and row["after"]["send"] is False


# ---------------------------------------------------------------------------------------------- Slack, webhooks, listening
def listed(api, name):
    """A channel on the team's Slack list (Settings > Tools > Slack)."""
    with api.app.state.store.transaction() as c:
        c.execute(
            "INSERT INTO slack_channels(key,name,created_at,updated_at) VALUES(?,?,?,?)",
            ("#" + name, name, "2026-10-28T00:00:00Z", "2026-10-28T00:00:00Z"),
        )


def test_slack_messages_reach_the_dm_or_channel_inbox(api):
    listed(api, "general")
    dm = make(api, {"type": "message", "inbox": "slack:dm"})
    general = make(api, {"type": "message", "inbox": "slack:#general"})
    person = {"id": "ana"}
    fire(
        api,
        automations.on_slack,
        {"channel": "D1", "channel_kind": "im", "ts": "1.1", "text": "hi"},
        person,
        {"kind": "im", "name": "DM"},
    )
    fire(
        api,
        automations.on_slack,
        {"channel": "C1", "channel_kind": "channel", "ts": "1.2", "text": "hi"},
        person,
        {"kind": "channel", "name": "#General"},
    )
    assert not fire(
        api,
        automations.on_slack,
        {"channel": "C2", "channel_kind": "channel", "ts": "1.3"},
        person,
        {"kind": "channel", "name": "#random"},
    )
    assert len(runs(api, dm)["runs"]) == 1 and len(runs(api, general)["runs"]) == 1


def test_a_webhook_starts_its_automation(api):
    hook = make(api, {"type": "webhook", "id": "trg_1"})
    out = fire(api, automations.on_webhook, "trg_1", "Deploys", "custom", "d1", {"ok": True}, "")
    assert out[0]["outcome"] == "automation" and out[0]["ok"]
    assert fire(api, automations.on_webhook, "trg_2", "Other", "custom", "d2", {}, "") == []
    assert len(runs(api, hook)["runs"]) == 1


# ---------------------------------------------------------------------------------------------- schedules and batches
def test_a_schedule_keeps_its_wall_clock_time_across_daylight_saving(api):
    with time_machine.travel(datetime(2026, 10, 31, 13, 30, tzinfo=timezone.utc), tick=False):
        aid = make(api, {"type": "schedule", "cron": "0 9 * * *", "tz": "America/New_York"})
    store = api.app.state.store
    automations.tick(store, datetime(2026, 10, 31, 13, 30, tzinfo=timezone.utc))  # after Saturday's 09:00 EDT
    assert get(api, f"automations/{aid}")["next_run"] == "2026-11-01T14:00:00.000000Z"  # Sunday 09:00 EST
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
    automations.tick(api.app.state.store, datetime(2026, 10, 21, 7, 5, tzinfo=timezone.utc))  # no pass before
    done = runs(api, aid)["runs"]
    assert len(done) == 1 and done[0]["trigger_ref"] == "schedule:2026-10-21T07:00:00.000000Z"


def test_a_batched_inbox_filters_on_receipt_and_runs_the_rest_at_the_batch_time(api, fake_gmail):
    gate = {"id": "gate", "type": "filter", "rules": [{"when": [], "then": [{"do": "label", "args": {"name": "x"}}]}]}
    with time_machine.travel(datetime(2026, 10, 28, 9, 0, tzinfo=timezone.utc), tick=False):
        aid = make(
            api,
            {"type": "message", "inbox": "ana@acme.example"},
            [gate, WAKE | {"args": {"bot": "ops"}}],
            batch={"times": ["13:00"]},
        )
        fire(api, automations.on_mail, "ana@acme.example", mail())
        held = runs(api, aid)["runs"][0]
        assert held["status"] == "waiting" and [s["id"] for s in held["steps"]] == ["gate"]
    with time_machine.travel(datetime(2026, 10, 28, 13, 0, 5, tzinfo=timezone.utc), tick=False):
        automations.tick(api.app.state.store)
        done = runs(api, aid)["runs"][0]
    # Every step ran, and the server labelled the message in Gmail.
    assert [(s["id"], s["status"]) for s in done["steps"]] == [("gate", "ok"), ("tell", "ok")]
    assert done["status"] == "ok"


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


# ---------------------------------------------------------------------------------------------- mail fields
NOOP = {
    "id": "sort",
    "type": "filter",
    "rules": [
        {"when": [{"field": "message.subject", "op": "contains", "value": "zz-never"}], "then": [{"do": "archive"}]}
    ],
}


def arrived(msg_id, sender, labels=("INBOX",), attachments=(), known_sender=None):
    return SimpleNamespace(
        **{
            **vars(mail(msg_id, sender=sender)),
            "labels": list(labels),
            "attachments": list(attachments),
            "known_sender": known_sender,
        }
    )


def ran(api, aid):
    return {r["trigger_ref"].rsplit(":", 1)[-1] for r in runs(api, aid)["runs"]}


def payload_of(api, aid, msg_id):
    with api.app.state.store.read() as c:
        row = c.execute(
            "SELECT saved_state FROM automation_runs WHERE automation_id=? AND trigger_ref=?",
            (aid, f"mail:ana@acme.example:{msg_id}"),
        ).fetchone()
    return json.loads(row["saved_state"])["payload"]["message"]


def test_mail_trigger_filters_read_the_category_domain_invite_and_known_sender(api):
    def by(field, op, value):
        return make(
            api,
            {"type": "message", "inbox": "ana@acme.example", "filters": [{"field": field, "op": op, "value": value}]},
            [NOOP],
        )

    promo = by("message.category", "is", "promotions")
    vendor = by("message.from_domain", "is", "vendor.example")
    invite = by("message.is_calendar_invite", "is", "true")
    known = by("message.known_sender", "is", "true")
    not_known = by("message.known_sender", "is_not", "true")
    arrivals = [
        arrived("a1", "Billing@Vendor.Example", labels=("INBOX", "CATEGORY_PROMOTIONS")),
        arrived(
            "a2",
            "pat@customer.example",
            labels=("INBOX", "CATEGORY_SOCIAL"),
            known_sender=True,
            attachments=[{"name": "invite.ics", "type": "text/calendar", "size": 10}],
        ),
        arrived(
            "a3",
            "cal@partner.example",
            attachments=[{"name": "meeting.ics", "type": "application/octet-stream"}],
            known_sender=False,
        ),
        arrived("a4", "news@other.example"),
    ]
    for message in arrivals:
        fire(api, automations.on_mail, "ana@acme.example", message)
    assert ran(api, promo) == {"a1"}
    assert ran(api, vendor) == {"a1"}
    assert ran(api, invite) == {"a2", "a3"}  # a text/calendar part, or an .ics file by name
    assert ran(api, known) == {"a2"}
    assert ran(api, not_known) == {"a1", "a3", "a4"}  # False and not-sent both read as not known


def test_on_mail_sets_the_fields_on_the_payload(api):
    aid = make(api, {"type": "message", "inbox": "ana@acme.example"}, [NOOP])
    fire(
        api,
        automations.on_mail,
        "ana@acme.example",
        arrived("p1", "Billing@Vendor.Example", labels=("INBOX", "CATEGORY_PERSONAL")),
    )
    fire(api, automations.on_mail, "ana@acme.example", arrived("p2", "nodomain", known_sender=True))
    primary = payload_of(api, aid, "p1")
    assert primary["from_domain"] == "vendor.example" and primary["category"] == "primary"
    assert primary["is_calendar_invite"] is False and primary["known_sender"] is None
    bare = payload_of(api, aid, "p2")
    assert bare["from_domain"] == "" and bare["category"] == "" and bare["known_sender"] is True


# ------------------------------------------------------------------------------------------- what mail counts as news
T0 = datetime(2026, 10, 28, 12, 0, tzinfo=timezone.utc)


DRAFT = {"id": "draft", "type": "llm", "as": "bot:ops", "prompt": "Sort this mail", "out": "reply"}


def test_forty_five_mail_turns_run_ten_at_a_time_through_the_real_executor(api):
    """The migrated inbox shape: one llm step, last. Its run ends while its turn is still going, so the turn holds the
    automation's slot until it answers. A turn that never answers frees its slot after TURN_SLOT_MINUTES."""
    r = runner(api)
    assign(api, r, "ops")
    ready(api, r, ["ops"])
    aid = make(api, {"type": "message", "inbox": "ana@acme.example"}, [DRAFT])
    store = api.app.state.store

    def counts():
        with store.read() as c:
            queued = c.execute(
                "SELECT count(*) FROM automation_runs WHERE automation_id=? AND status='queued'", (aid,)
            ).fetchone()[0]
            holds = c.execute("SELECT count(*) FROM automation_holds WHERE automation_id=?", (aid,)).fetchone()[0]
        return queued, holds

    with time_machine.travel(T0, tick=False):
        with store.transaction() as c:
            for i in range(45):  # the server's check of the mailbox hears 45 new messages
                automations.on_mail(c, "ana@acme.example", mail(msg_id=f"m{i:02d}", subject="Invoice"))
        store.automations.drain()
        assert counts() == (35, 10)  # ten turns out, the other 35 wait queued
        finish(api, r)  # one turn answers: its slot is free
        store.automations.drain()
        assert counts() == (34, 10)  # the eleventh turn starts, and only one
    with time_machine.travel(T0 + timedelta(minutes=automations.TURN_SLOT_MINUTES + 1), tick=False):
        store.automations.drain()  # the turns that never answered: slots lapse
        assert counts() == (24, 20)  # ten more start; the old holds wait for the sweep
        with store.read() as c:
            assert c.execute("SELECT count(*) FROM automation_runs WHERE automation_id=?", (aid,)).fetchone()[0] == 45


def test_a_long_mailbox_keeps_each_message_distinct_and_each_runs_once(api):
    """A 194-character mailbox leaves the 200-character ref no room for the message id: the stored ref keeps a digest
    of the whole, so two messages from it are two runs, and a message offered again is still one."""
    box = "ana." + "x" * 177 + "@acme.example"
    assert len(box) == 194
    # The owner can read only her own inbox, so the automation sits on it; the offers carry the long mailbox, which is
    # what the ref is built from, and the match takes them all.
    aid = make(api, {"type": "message", "inbox": "ana@acme.example"}, [NOOP])

    def offer(msg_id):
        payload = {"message": {"source": "mail", "mailbox": box, "id": msg_id}}
        fire(api, automations.fire, "message", payload, f"mail:{box}:{msg_id}", lambda t: True, lambda row: True)

    offer("m1")
    offer("m2")
    offer("m1")
    with api.app.state.store.read() as c:
        refs = [r[0] for r in c.execute("SELECT trigger_ref FROM automation_runs WHERE automation_id=?", (aid,))]
    assert len(refs) == 2 and len(set(refs)) == 2 and max(map(len, refs)) <= 200


def test_a_check_stamped_with_an_offset_sets_the_watch_at_the_same_instant_as_utc(api):
    with api.app.state.store.read() as c:
        utc = automations.mail_watch(c, "ana@acme.example", "2026-10-28T12:00:00+00:00")
        local = automations.mail_watch(c, "ana@acme.example", "2026-10-29T02:00:00+14:00")
    assert utc == local == "2026-10-27T12:00:00.000000Z"


def test_an_automation_has_ten_runs_in_flight_and_the_rest_wait_queued(api):
    """A backlog starts ten runs at a time. A run waiting on a turn holds its place until the turn's answer is taken;
    one waiting for a batch time (its wake time set) holds none."""
    aid = make(api, {"type": "task_received", "to": "bot:ops"})
    store = api.app.state.store
    hub = store.automations
    with store.transaction() as c:
        for i in range(12):
            c.execute(
                "INSERT INTO automation_runs(id,automation_id,version,trigger_ref,started_at,status,saved_state,"
                "chain) VALUES(?,?,?,?,?,?,?,?)",
                (f"run_q{i:02d}", aid, 1, f"task:q{i}", f"2026-10-28T09:00:{i:02d}.000000Z", "queued", "{}", "[]"),
            )
    claimed = [hub.claim() for _ in range(10)]
    assert None not in claimed and len(set(claimed)) == 10
    assert hub.claim() is None  # ten in flight: the other two wait, queued
    with store.transaction() as c:
        c.execute("UPDATE automation_runs SET status='ok' WHERE id=?", (claimed[0].id,))
    assert hub.claim() is not None  # one finished: one more starts
    with store.transaction() as c:
        c.execute(
            "UPDATE automation_runs SET status='waiting', wake_at=? WHERE id=?",
            (H.shift(H.now(), days=1), claimed[1].id),
        )
    assert hub.claim() is not None  # waiting for a batch time holds no place
    assert hub.claim() is None
