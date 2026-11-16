"""Learn keeps bounded items for Update, with source checks, spend and delivery receipts."""

import importlib.util
import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import sqlite3
import threading

import pytest

from backend import judge, learnings, maintenance, market, slack_gateway, threads, updates
from backend.docs import Docs
from backend.maintenance_steps import learn as step
from backend.maintenance_steps import update as update_step
from backend.store import H
from backend.tests.helpers.api import claim, headers, post
from backend import bot_settings as BS
from backend.tests.helpers.bot_contact import bot
from backend.tests.helpers.fake_gmail import fake_gmail  # noqa: F401
from backend.tests.helpers.mail_api import put_mail
from backend.tests.helpers.maintenance_learn_step import complete, queue, start


def context(api, **changes):
    now = H.now()
    ctx = {
        "run_id": H.new_id(),
        "window_start": H.shift(now, days=-1),
        "window_end": now,
        "now": now,
        "settings": {"key": "learn", "enabled": 1, "frequency": "day"},
        "cap": 0.0,
        "store": api.app.state.store,
        "auth": api.app.state.auth,
    }
    ctx.update(changes)
    return {"step_id": ctx["run_id"] + ":learn", **ctx}


def run_step(api, ctx=None):
    store = api.app.state.store
    ctx = ctx or context(api)
    with store.transaction() as c:
        c.execute(
            "INSERT OR IGNORE INTO maintenance_runs(id,occurrence_key,trigger,started_at,heartbeat_at) "
            "VALUES(?,?,'run_now',?,?)",
            (ctx["run_id"], ctx["run_id"], ctx["now"], ctx["now"]),
        )
        c.execute(
            "INSERT OR IGNORE INTO maintenance_run_steps(id,maintenance_run_id,step,status,started_at) "
            "VALUES(?,?,'learn','pending',?)",
            (ctx["step_id"], ctx["run_id"], ctx["now"]),
        )
    with store.read() as c:
        c.execute("PRAGMA query_only=ON")
        try:
            return step.run(c, ctx)
        finally:
            c.execute("PRAGMA query_only=OFF")


def scoring(api, monkeypatch, count=20, recipients=("ops",)):
    store = api.app.state.store
    with store.transaction() as c:
        task = H.task_create(
            c, "human:ana", "Invoice rules", "Friday is the cutoff.", "bot:ops", lint=False, private=False
        )
    items = [
        {
            "key": f"task:{n}",
            "source": "task",
            "author": "human:ana",
            "ref": {"task": task["id"]},
            "created_at": H.now(),
            "label": f"Rule {n}",
            "text": f"Keep rule {n}.\nIts source is explicit.",
            "audience": None,
        }
        for n in range(count)
    ]
    team = {recipient: {"name": recipient, "profile": recipient} for recipient in recipients}
    calls = []

    def engine(state, questions, label=None):
        calls.append(state)
        value = 0.6 + int(state["item"]["label"].split()[-1]) / 1000
        return {"answers": {key: {"type": "noul", "noul": value} for key in questions}, "cost_usd": 0.002}

    monkeypatch.setattr(learnings, "engine_for", lambda *args: engine)
    monkeypatch.setattr(learnings, "roster", lambda *args: team)
    monkeypatch.setattr(learnings, "collect", lambda *args: ([dict(i) for i in items], 0))
    monkeypatch.setattr(
        learnings,
        "question_set",
        lambda *args: {
            "label": "learnings-route",
            "questions": {"learnable": {"type": "noul", "instructions": "Durable?"}},
            "thresholds": {"noise": 0.2, "relevant": 0.6, "per_recipient": 99},
        },
    )
    return task, calls


def keep_items(c, items, created_at=None):
    now, run_id = created_at or H.now(), H.new_id()
    c.execute("INSERT INTO learning_runs(id,night,status,started_at) VALUES(?,?,'done',?)", (run_id, now[:10], now))
    items = [
        {
            "author": "human:ana",
            "created_at": now,
            "label": item["key"],
            "text": "Keep this rule.",
            "learnable": 0.9,
            "scores": {"ops": 0.9},
            **item,
        }
        for item in items
    ]
    learnings.dispatch(c, run_id, now[:10], items, {"ops": items}, {}, {})
    c.execute("UPDATE learning_deliveries SET created_at=? WHERE run_id=?", (now, run_id))
    return run_id


def test_top_fifteen_per_bot_cost_and_replay_without_packet_tasks(api, monkeypatch):
    bot(api, "reader", "Reader")
    _, calls = scoring(api, monkeypatch, recipients=("ops", "reader"))
    store, ctx = api.app.state.store, context(api)
    with store.read() as c:
        before = c.execute("SELECT count(*) FROM tasks").fetchone()[0]
    result = run_step(api, ctx)
    assert result["status"] == "ran" and result["checked"] == 20 and result["found"] == 15
    assert result["woke"] == 0 and result["cost_usd"] == pytest.approx(0.04)
    assert result["details"]["items_read"] == 20 and result["details"]["items_sent"] == {}
    assert len(calls) == 20
    assert run_step(api, ctx)["cost_usd"] == result["cost_usd"]
    assert len(calls) == 20
    with store.read() as c:
        rows = c.execute("SELECT * FROM learning_deliveries WHERE run_id=?", (ctx["run_id"],)).fetchall()
        assert {r["recipient"] for r in rows} == {"ops", "reader"}
        for row in rows:
            keys = json.loads(row["item_keys"])
            assert len(keys) == 15 and keys[0] == "task:19" and "task:0" not in keys
            assert row["task_id"] is None and row["update_batch_id"] is None
        assert c.execute("SELECT count(*) FROM tasks").fetchone()[0] == before


def test_maintenance_runs_learn_and_moves_its_cursor_with_its_log(api, monkeypatch):
    _, calls = scoring(api, monkeypatch, count=2)
    store = api.app.state.store
    with store.transaction() as c:
        maintenance.save_step(c, "human:ana", {"key": "learn", "enabled": True})
    run, started = maintenance.run_now(store, api.app.state.auth, "human:ana", step="learn", wait=True)
    assert started and run["ok"] and len(calls) == 2
    [row] = run["steps"]
    assert row["step"] == "learn" and row["status"] == "ran" and row["checked"] == 2
    assert row["cost_usd"] == pytest.approx(0.004)
    with store.read() as c:
        settings = c.execute("SELECT * FROM maintenance_steps WHERE key='learn'").fetchone()
        assert settings["cursor_at"] == row["details"]["window_end"] and settings["last_ran_at"]
        assert not maintenance.due(settings, H.parse_ts(settings["last_started_at"]))
        assert c.execute("SELECT read_count FROM learning_runs WHERE id=?", (run["id"],)).fetchone()[0] == 2
        assert c.execute("SELECT task_id FROM learning_deliveries WHERE run_id=?", (run["id"],)).fetchone()[0] is None


def test_recovered_learn_worker_cannot_deliver_or_advance_after_scoring(api, monkeypatch):
    scoring(api, monkeypatch, count=1)
    store, auth = api.app.state.store, api.app.state.auth
    entered, release, heartbeat = threading.Event(), threading.Event(), threading.Event()
    original_engine = learnings.engine_for(store, None)
    original_claimed = maintenance._still_pending

    def engine(*args, **kwargs):
        if not entered.is_set():
            entered.set()
            assert release.wait(10)
        return original_engine(*args, **kwargs)

    def claimed(c, row_id):
        result = original_claimed(c, row_id)
        if threading.current_thread().name == "maintenance-heartbeat":
            heartbeat.set()
        return result

    monkeypatch.setattr(learnings, "engine_for", lambda *args: engine)
    monkeypatch.setattr(maintenance, "_still_pending", claimed)
    monkeypatch.setattr(maintenance, "HEARTBEAT_SECONDS", 0.01)
    with store.transaction() as c:
        maintenance.save_step(c, "human:ana", {"key": "learn", "enabled": True})
    with ThreadPoolExecutor(max_workers=1) as workers:
        old_worker = workers.submit(maintenance.run_now, store, auth, "human:ana", step="learn", wait=True)
        try:
            assert entered.wait(5) and heartbeat.wait(5)
            with store.transaction() as c:
                old = c.execute("SELECT * FROM maintenance_runs WHERE finished_at IS NULL").fetchone()
                assert old["heartbeat_at"] > old["started_at"]
                # Simulate a stopped heartbeat while the model still has its request.
                c.execute(
                    "UPDATE maintenance_runs SET heartbeat_at=? WHERE id=?", (H.shift(H.now(), minutes=-31), old["id"])
                )
                maintenance.recover(c, H.now())
            replacement, started = maintenance.run_now(store, auth, "human:ana", step="learn", wait=True)
            assert started and replacement["ok"]
            with store.read() as c:
                cursors = [tuple(r) for r in c.execute("SELECT * FROM learning_cursors ORDER BY source")]
        finally:
            release.set()
        abandoned, _ = old_worker.result(timeout=5)
    assert abandoned["result"] == "abandoned" and abandoned["steps"][0]["reason"] == "abandoned"
    with store.read() as c:
        assert c.execute("SELECT id FROM learning_runs").fetchall()[0][0] == replacement["id"]
        assert [r[0] for r in c.execute("SELECT run_id FROM learning_deliveries")] == [replacement["id"]]
        assert not c.execute("SELECT 1 FROM learning_runs WHERE id=?", (old["id"],)).fetchone()
        assert [tuple(r) for r in c.execute("SELECT * FROM learning_cursors ORDER BY source")] == cursors


def test_finished_learn_step_writes_no_deliveries_or_cursors(api, monkeypatch):
    scoring(api, monkeypatch, count=1)
    ctx = context(api)
    with api.app.state.store.transaction() as c:
        c.execute(
            "INSERT INTO maintenance_runs(id,occurrence_key,trigger,started_at,heartbeat_at) VALUES(?,?,'run_now',?,?)",
            (ctx["run_id"], ctx["run_id"], ctx["now"], ctx["now"]),
        )
        c.execute(
            "INSERT INTO maintenance_run_steps(id,maintenance_run_id,step,status,started_at,finished_at) "
            "VALUES(?,?,'learn','pending',?,?)",
            (ctx["step_id"], ctx["run_id"], ctx["now"], H.now()),
        )
    assert run_step(api, ctx)["reason"] == "claim lost"
    with api.app.state.store.read() as c:
        for table in ("learning_runs", "learning_items", "learning_deliveries", "learning_cursors"):
            assert c.execute(f"SELECT count(*) FROM {table}").fetchone()[0] == 0


def test_maintenance_listing_shows_seven_day_delivery_stats_on_learn(api, monkeypatch):
    scoring(api, monkeypatch, count=4)
    run_step(api)
    store = api.app.state.store
    with store.transaction() as c:
        row = queue(c)
        updates.send_request(c, row, delivery=True)
        learnings.record_saved(c, "ops", row["id"], 2)
        old = H.shift(H.now(), days=-8)
        c.execute(
            "INSERT INTO learning_runs(id,night,status,started_at,read_count,detail) VALUES('old',?,'quiet',?,99,?)",
            (old[:10], old, json.dumps({"cost_usd": 99.0})),
        )
    reply = api.get("/api/v2/maintenance", headers=headers())
    assert reply.status_code == 200, reply.text
    steps = {row["key"]: row for row in reply.json()["steps"]}
    stats = steps["learn"]["stats"]
    assert (stats["days"], stats["runs"], stats["items_read"]) == (7, 1, 4)
    assert stats["items_sent"] == {"ops": 4} and (stats["saved"], stats["dropped"]) == (2, 2)
    assert stats["cost_usd"] == pytest.approx(0.008)
    assert all("stats" not in row for key, row in steps.items() if key != "learn")


def test_cap_reached_mid_run_keeps_cursors_and_reports_paid_failures(api, monkeypatch):
    scoring(api, monkeypatch, count=3)

    def capped(*args):
        exc = learnings.J.JudgeError("spend_cap", "monthly cap", 429)
        exc.cost_usd = 0.03
        raise exc

    monkeypatch.setattr(learnings, "engine_for", lambda *args: capped)
    result = run_step(api)
    assert result["status"] == "skipped" and result["cost_usd"] == pytest.approx(0.09)
    assert result["details"]["failed"] == {"spend_cap": 3}
    with api.app.state.store.read() as c:
        assert c.execute("SELECT count(*) FROM learning_deliveries").fetchone()[0] == 0
        assert c.execute("SELECT count(*) FROM learning_cursors").fetchone()[0] == 0


def test_update_sends_fifteen_across_runs_and_binds_the_exact_batch_once(api, monkeypatch):
    scoring(api, monkeypatch)
    store = api.app.state.store
    run_step(api)
    run_step(api)  # A custom frequency can leave several pending runs on one day.
    with store.transaction() as c:
        assert update_step.plan_request(c, "ops", updates.today()) == "deliver"
        row = queue(c)
        message = updates.send_request(c, row, delivery=True)
        assert "Learnings for you:" in message["body"] and row["id"] in message["body"]
        assert len([line for line in message["body"].splitlines() if line.startswith("- Rule ")]) == 15
        rows = c.execute("SELECT * FROM learning_deliveries WHERE recipient='ops'").fetchall()
        assert {r["update_batch_id"] for r in rows} == {row["id"]}
        assert {r["message_id"] for r in rows} == {message["id"]}
        assert sum(len(json.loads(r["sent_item_keys"])) for r in rows) == 15
        assert updates.request_slots(c, "ops")["learnings"] == []
        retry = updates.send_request(c, row, delivery=True)
        assert retry["body"] == message["body"]
        assert {r[0] for r in c.execute("SELECT message_id FROM learning_deliveries")} == {message["id"]}
        detail = json.loads(c.execute("SELECT detail FROM learning_runs LIMIT 1").fetchone()[0])
        assert detail["items_sent"]["ops"] <= 15


def test_a_refused_request_leaves_items_pending(api, monkeypatch):
    scoring(api, monkeypatch, count=2)
    run_step(api)

    def refuse(*args, **kwargs):
        raise RuntimeError("request refused")

    monkeypatch.setattr(updates.H, "say", refuse)
    with pytest.raises(RuntimeError, match="request refused"):
        with api.app.state.store.transaction() as c:
            updates.send_request(c, queue(c), delivery=True)
    with api.app.state.store.read() as c:
        assert c.execute("SELECT update_batch_id FROM learning_deliveries").fetchone()[0] is None
        assert len(learnings.pending(c, "ops")) == 2


def test_no_pending_items_add_no_learning_block_and_redos_do_not_consume_items(api, monkeypatch):
    store = api.app.state.store
    with store.read() as c:
        empty = updates.request_text(c, "ops", "daily", updates.today())
        assert "Learnings for you" not in empty and "learnings_saved" not in empty
    scoring(api, monkeypatch, count=2)
    run_step(api)
    with store.transaction() as c:
        text = updates.request_text(c, "ops", "weekly", "2026-10-30")
        assert "Learnings for you" in text
        row = dict(queue(c))
        row["redo"] = 1
        text = updates.send_request(c, row)["body"]
        assert "Learnings for you" not in text
        assert c.execute("SELECT update_batch_id FROM learning_deliveries").fetchone()[0] is None


def test_private_or_deleted_sources_stay_out_of_company_update(api, monkeypatch):
    task, _ = scoring(api, monkeypatch, count=1)
    run_step(api)
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE tasks SET private=1 WHERE id=?", (task["id"],))
        assert updates.request_slots(c, "ops")["learnings"] == []
        assert update_step.plan_request(c, "ops", updates.today()) == "skip"
        assert "Keep rule" not in updates.request_text(c, "ops", "daily", updates.today())
        c.execute("UPDATE tasks SET private=0 WHERE id=?", (task["id"],))
        assert len(updates.request_slots(c, "ops")["learnings"]) == 1
        c.execute("DELETE FROM tasks WHERE id=?", (task["id"],))
        assert updates.request_slots(c, "ops")["learnings"] == []


def test_a_nights_sent_mail_is_read_from_gmail_and_a_silent_mailbox_is_passed_over(api, fake_gmail):
    from backend import mailbox_access as MA

    store = api.app.state.store
    with store.transaction() as c:
        for person in ("ana", "ben"):
            MA.ensure_mailbox(c, person + "@acme.example", person_id=person, created_by="human:ana")
    window = (1_783_000_000, 1_783_100_000)
    put_mail(api, "ana@acme.example", msg_id="sent", from_addr="ana@acme.example", labels=["SENT"], epoch=1_783_050_000)
    put_mail(
        api, "ana@acme.example", msg_id="older", from_addr="ana@acme.example", labels=["SENT"], epoch=1_782_000_000
    )
    put_mail(api, "ana@acme.example", msg_id="received", labels=["INBOX"], epoch=1_783_050_000)
    put_mail(
        api, "ben@acme.example", msg_id="ben-sent", from_addr="ben@acme.example", labels=["SENT"], epoch=1_783_050_000
    )
    fake_gmail.down.add("ben@acme.example")
    people = {"ana@acme.example": {"id": "ana", "name": "Ana"}, "ben@acme.example": {"id": "ben", "name": "Ben"}}
    since, now = (learnings.utc(datetime_of(t)) for t in window)
    with store.read() as c:
        items = learnings._sent_mail(c, people, since, now)
        assert c.execute("SELECT count(*) FROM mail_messages").fetchone()[0] == 0
    assert [(i["key"], i["author"]) for i in items] == [("mail:ana@acme.example:sent", "human:ana")]
    assert "How does pricing work?" in items[0]["text"]


def datetime_of(epoch):
    from datetime import datetime, timezone

    return datetime.fromtimestamp(epoch, timezone.utc)


def test_only_what_the_company_may_read_reaches_the_company_update(api, fake_gmail):
    """One install, each source in turn (public and private Slack channels, personal sent mail, task comments, a direct
    chat that references a public task, the bot's home with restricted history), the learning rows cleared between."""
    store = api.app.state.store

    def fresh():
        with store.transaction() as c:
            for table in [
                r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'learning_%'")
            ]:
                c.execute(f"DELETE FROM {table}")

    # Only known public Slack messages.
    with api.app.state.store.transaction() as c:
        items = []
        for channel, channel_type in (("C_PUBLIC", "channel"), ("C_PRIVATE", "group"), ("C_UNKNOWN", None)):
            event, why = slack_gateway.event_from(
                {
                    "event_id": channel,
                    "team_id": "T_ACME",
                    "event": {
                        "type": "message" if channel_type else "app_mention",
                        "channel_type": channel_type,
                        "channel": channel,
                        "ts": "1.0",
                        "user": "U_ANA",
                        "text": "Keep this rule.",
                    },
                },
                "U_TICO",
            )
            assert why is None and event["channel_kind"] == "channel"
            c.execute(
                "INSERT INTO slack_events(event_id,team_id,channel,channel_kind,thread_ts,ts,user_id,event_type,"
                "text,received_at) VALUES(?,?,?,'channel','1.0','1.0','U_ANA',?,'Keep this rule.',?)",
                (channel, "T_ACME", channel, event["event_type"], H.now()),
            )
            items.append({"key": channel, "source": "slack", "ref": {"channel": channel, "ts": "1.0"}})
        keep_items(c, items)
        assert [i["item_key"] for i in learnings.pending(c, "ops")] == ["C_PUBLIC"]

    fresh()  # Personal sent mail.
    mail = put_mail(
        api, "ana@acme.example", from_addr="ana@acme.example", labels=["SENT"], body="Personal salary plans."
    )
    with api.app.state.store.transaction() as c:
        keep_items(
            c,
            [
                {
                    "key": "personal-mail",
                    "source": "mail",
                    "ref": {"mailbox": "ana@acme.example", "msg_id": mail["msg_id"]},
                }
            ],
        )
        assert updates.request_slots(c, "ops")["learnings"] == []

    fresh()  # A real public task comment passes provenance, a private one does not.
    with api.app.state.store.transaction() as c:
        items = []
        for private in (False, True):
            title = "Private invoice rules" if private else "Public invoice rules"
            task = H.task_create(c, "human:ana", title, "Friday is the cutoff.", "bot:ops", lint=False, private=private)
            comment = H.task_comment(c, "human:ana", task["id"], "Keep the Friday cutoff.", wake=False)
            assert H.message(c, comment["id"])["restricted"] == 1
            items.append(
                {"key": "private" if private else "public", "source": "task", "ref": {"message": comment["id"]}}
            )
        keep_items(c, items)
        assert [i["item_key"] for i in learnings.pending(c, "ops")] == ["public"]
        c.execute("UPDATE messages SET deleted_at=? WHERE id=?", (H.now(), items[0]["ref"]["message"]))
        assert learnings.pending(c, "ops") == []

    fresh()  # A direct chat referencing a public task.
    with store.transaction() as c:
        task = H.task_create(
            c, "human:ana", "Chat invoice rules", "Friday is the cutoff.", "bot:ops", lint=False, private=False
        )
        room = H.open_conversation(c, "human:ana", ["human:ana", "bot:ops"], scope="direct")
        message = H.say(
            c,
            "human:ana",
            "bot:ops",
            "Keep this correction between us.",
            conversation_id=room["id"],
            refs={"task": task["id"]},
        )
        c.execute("UPDATE conversations SET audience=NULL WHERE id=?", (room["id"],))
        assert H.conversation(c, room["id"])["audience"] is None
        items, _ = learnings.collect(c, store.settings, H.now(), {"ops": {"name": "Ops"}})
        [item] = [i for i in items if i["ref"].get("message") == message["id"]]
        assert item["source"] == "task" and item["audience"] is None
        keep_items(c, [item])
        assert learnings.pending(c, "ops") == []
        assert "between us" not in updates.request_text(c, "ops", "daily", updates.today())

    fresh()  # The bot's home corrections follow home access and message privacy.
    with store.transaction() as c:
        home = threads.home(c, "bot:ops")
        public = H.say(c, "human:ana", "bot:ops", "Use the Friday cutoff.", conversation_id=home["id"])
        legacy = H.say(c, "human:ana", "bot:ops", "Old restricted history.", conversation_id=home["id"])
        c.execute("UPDATE messages SET legacy_audience='shared:ops' WHERE id=?", (legacy["id"],))
        task = H.task_create(
            c, "human:ana", "Private billing rules", "Personal billing.", "bot:ops", lint=False, private=True
        )
        private = H.say(
            c,
            "human:ana",
            "bot:ops",
            "Keep the private billing rule.",
            conversation_id=home["id"],
            refs={"task": task["id"]},
        )
        ids = {m["id"] for m in (public, legacy, private)}
        items, _ = learnings.collect(c, store.settings, H.now(), {"ops": {"name": "Ops"}})
        corrections = [i for i in items if i["ref"].get("message") in ids]
        assert len(corrections) == 3
        keep_items(c, corrections)
        assert [i["ref"]["message"] for i in learnings.pending(c, "ops")] == [public["id"]]
        c.execute(
            "UPDATE bot_config SET access=? WHERE bot='ops'",
            (json.dumps({"read": {"everyone": False, "people": ["ana"]}}),),
        )
        assert learnings.pending(c, "ops") == []
        c.execute("UPDATE bot_config SET access=NULL WHERE bot='ops'")
        request = updates.send_request(c, queue(c), delivery=True)
        assert "Use the Friday cutoff." in request["body"]
        assert "Old restricted history" not in request["body"] and "private billing rule" not in request["body"]


def test_completed_delivery_parses_saved_count_and_logs_stats_once(api, monkeypatch):
    scoring(api, monkeypatch, count=4)
    store, ctx = api.app.state.store, context(api)
    run_step(api, ctx)
    with store.transaction() as c:
        row = queue(c)
        updates.send_request(c, row, delivery=True)
        sent = c.execute("SELECT * FROM update_queue WHERE id=?", (row["id"],)).fetchone()
    turn = start(api, sent)
    complete(api, turn, "Kept two rules.\nlearnings_saved: 2")
    with store.transaction() as c:
        attempt = c.execute("SELECT * FROM attempts WHERE id=?", (turn[1]["id"],)).fetchone()
        assert attempt["state"] == "completed"
        assert all(attempt[key] is not None for key in ("runner_id", "generation", "token_hash", "lease_until"))
        assert updates.settle_sent(c, sent) is False
        assert (
            c.execute("SELECT state FROM update_queue WHERE id=?", (row["id"],)).fetchone()[0]
            == "delivered_without_update"
        )
        delivery = c.execute("SELECT * FROM learning_deliveries").fetchone()
        assert delivery["learnings_saved"] == 2 and delivery["settled_at"]
        learnings.record_saved(c, "ops", row["id"], 0)
        stats = learnings.beta_stats(c)
        assert stats["items_read"] == 4 and stats["items_sent"] == {"ops": 4}
        assert (stats["saved"], stats["dropped"], stats["unreported"]) == (2, 2, 0)
        assert stats["cost_usd"] == pytest.approx(0.008)
        detail = json.loads(c.execute("SELECT detail FROM learning_runs WHERE id=?", (ctx["run_id"],)).fetchone()[0])
        assert detail["items_sent"] == {"ops": 4} and detail["saved"] == detail["dropped"] == 2


def test_structured_post_feedback_is_scoped_to_its_bot_and_batch(api, monkeypatch):
    scoring(api, monkeypatch, count=3)
    run_step(api)
    with api.app.state.store.transaction() as c:
        row = queue(c, day="2026-11-04")
        updates.send_request(c, row)
        updates.post(
            c, "ops", "- Finished invoices", day=row["day"], learnings_saved=3, delivery_batch_id="other-batch"
        )
        assert c.execute("SELECT learnings_saved FROM learning_deliveries").fetchone()[0] is None
        updates.post(c, "ops", "- Finished invoices", day=row["day"], learnings_saved=1, delivery_batch_id=row["id"])
        sent = c.execute("SELECT * FROM update_queue WHERE id=?", (row["id"],)).fetchone()
        assert updates.settle_sent(c, sent) is False
        assert c.execute("SELECT learnings_saved FROM learning_deliveries").fetchone()[0] == 1


def test_final_feedback_after_a_post_is_collected_by_the_next_queue_tick(api, monkeypatch):
    scoring(api, monkeypatch, count=2)
    run_step(api)
    store = api.app.state.store
    with store.transaction() as c:
        row = queue(c, day="2026-11-04")
        updates.send_request(c, row)
        sent = c.execute("SELECT * FROM update_queue WHERE id=?", (row["id"],)).fetchone()
    turn = start(api, sent)
    with store.transaction() as c:
        updates.post(c, "ops", "- Finished invoices", day=row["day"])
        updates.settle_sent(c, sent)
    complete(api, turn, "learnings_saved: 1")
    with store.transaction() as c:
        updates.settle_learnings(c)
        assert c.execute("SELECT learnings_saved FROM learning_deliveries").fetchone()[0] == 1


@pytest.mark.parametrize("first_state", ["canceled", "completed"])
def test_delivery_retry_reports_its_final_saved_count(api, monkeypatch, first_state):
    scoring(api, monkeypatch, count=2)
    run_step(api)
    store = api.app.state.store
    with store.transaction() as c:
        row = queue(c)
        updates.send_request(c, row)
        sent = c.execute("SELECT * FROM update_queue WHERE id=?", (row["id"],)).fetchone()
        if first_state == "canceled":
            c.execute("UPDATE jobs SET state='canceled' WHERE message_id=?", (sent["message_id"],))
    first_turn = start(api, sent) if first_state == "completed" else None
    if first_turn:
        complete(api, first_turn, "No update posted.")
    with store.transaction() as c:
        updates.settle_learnings(c)
        assert updates.settle_sent(c, sent) is False
        retry = c.execute("SELECT * FROM update_queue WHERE id=?", (row["id"],)).fetchone()
        assert retry["state"] == "queued" and retry["tries"] == 1
        message = updates.send_request(c, retry, delivery=True)
        assert message["id"] != sent["message_id"]
        retry = c.execute("SELECT * FROM update_queue WHERE id=?", (row["id"],)).fetchone()
        assert c.execute("SELECT feedback_message_id FROM learning_deliveries").fetchone()[0] == retry["message_id"]
        assert c.execute("SELECT message_id FROM learning_deliveries").fetchone()[0] == sent["message_id"]
        updates.settle_learnings(c)
        assert c.execute("SELECT settled_at FROM learning_deliveries").fetchone()[0] is None
    complete(api, start(api, retry, first_turn[0] if first_turn else None), "learnings_saved: 1")
    with store.transaction() as c:
        updates.settle_sent(c, retry)
        updates.settle_learnings(c)
        assert (
            c.execute("SELECT state FROM update_queue WHERE id=?", (row["id"],)).fetchone()[0]
            == "delivered_without_update"
        )
        delivery = c.execute("SELECT learnings_saved,settled_at FROM learning_deliveries").fetchone()
        assert delivery["learnings_saved"] == 1 and delivery["settled_at"]
        stats = learnings.beta_stats(c)
        assert (stats["saved"], stats["dropped"], stats["unreported"]) == (1, 1, 0)


@pytest.mark.parametrize("resend", [False, True])
def test_redo_keeps_the_original_delivery_turn_for_feedback_and_history(api, monkeypatch, resend):
    with api.app.state.store.transaction() as c:
        # The built-in Librarian on a computer, as in test_librarian: its Docs and Market writes are what Learn reads.
        c.execute(
            "INSERT INTO bots(id,slug,display_name,effort,state,created_at) "
            "VALUES('librarian','librarian','Librarian','','active',?)",
            (H.now(),),
        )
        c.execute("INSERT INTO bot_config(bot,team,owner_id) VALUES('librarian',NULL,'ana')")
        BS.write(c, "librarian", {"runtime": "fake", "host": "keeper"})
    scoring(api, monkeypatch, count=2, recipients=("librarian",))
    run_step(api)
    store = api.app.state.store
    docs = Docs(api.app, store, api.app.state.auth, None)
    with store.transaction() as c:
        row = queue(c, bot_id="librarian")
        updates.send_request(c, row, delivery=True)
        sent = c.execute("SELECT * FROM update_queue WHERE id=?", (row["id"],)).fetchone()
    turn = start(api, sent)
    post(
        api,
        "bots/librarian/memory/report",
        {
            "commits": [
                {
                    "sha": "a" * 40,
                    "subject": "Keep the invoice cutoff",
                    "author": "librarian",
                    "committed": H.now(),
                    "files": ["memory/learnings.md"],
                    "diff": "+ Keep the Friday cutoff",
                    "attempt": turn[1]["id"],
                }
            ]
        },
        turn[0]["token"],
    )
    with store.transaction() as c:
        doc = docs.insert(c, "bot:librarian", "Invoice policy", "Friday is the cutoff.")
        evidence, _ = market.create_evidence(c, "bot:librarian", quote="Invoices close on Friday.")
        entity, _ = market.create_entity(
            c, "bot:librarian", type="organization", name="Acme Billing", evidence_ids=[evidence["id"]]
        )
        updates.redo(c, row["kind"], row["day"], bots=["librarian"])
        redo = c.execute("SELECT * FROM update_queue WHERE id=?", (row["id"],)).fetchone()
        assert redo["message_id"] is None
        if resend:
            assert updates.send_request(c, redo)["id"] != sent["message_id"]
        assert c.execute("SELECT feedback_message_id FROM learning_deliveries").fetchone()[0] == sent["message_id"]
        assert c.execute("SELECT message_id FROM learning_deliveries").fetchone()[0] == sent["message_id"]
        delivery = c.execute("SELECT * FROM learning_deliveries").fetchone()
        assert [d["id"] for d in learnings.doc_subjects(c, delivery, "")] == [doc["id"]]
        assert [e["id"] for e in learnings.market_subjects(c, delivery, "")] == [entity["id"]]
    complete(api, turn, "learnings_saved: 1")
    with store.transaction() as c:
        docs.insert(c, "bot:librarian", "Unrelated policy", "Outside the delivery turn.")
        market.create_entity(
            c, "bot:librarian", type="organization", name="Acme Shipping", evidence_ids=[evidence["id"]]
        )
        updates.settle_learnings(c)
        assert c.execute("SELECT learnings_saved FROM learning_deliveries").fetchone()[0] == 1
        run_id = c.execute("SELECT run_id FROM learning_deliveries").fetchone()[0]
        assert learnings.delivery_details(c, run_id)["saved"] == 1
        delivery = c.execute("SELECT * FROM learning_deliveries").fetchone()
        assert learnings.delivery_attempts(c, delivery) == [turn[1]["id"]]
        assert [e["title"] for e in learnings.bot_entries(c, delivery)] == ["Keep the invoice cutoff"]
        before = learnings.window(c, delivery)
        assert before[0] and before[1] < H.now()
        assert [d["id"] for d in learnings.doc_subjects(c, delivery, "")] == [doc["id"]]
        assert [e["id"] for e in learnings.market_subjects(c, delivery, "")] == [entity["id"]]
        c.execute("DELETE FROM update_queue WHERE id=?", (row["id"],))
        assert learnings.delivery_attempts(c, delivery) == [turn[1]["id"]]
        assert learnings.window(c, delivery) == before
        assert [d["id"] for d in learnings.doc_subjects(c, delivery, "")] == [doc["id"]]
        assert [e["id"] for e in learnings.market_subjects(c, delivery, "")] == [entity["id"]]


def test_missing_or_out_of_range_feedback_is_unreported(api, monkeypatch):
    scoring(api, monkeypatch, count=2)
    run_step(api)
    store = api.app.state.store
    with store.transaction() as c:
        row = queue(c)
        updates.send_request(c, row, delivery=True)
        sent = c.execute("SELECT * FROM update_queue WHERE id=?", (row["id"],)).fetchone()
    complete(api, start(api, sent), "Nothing new.")
    with store.transaction() as c:
        updates.settle_sent(c, sent)
        learnings.record_saved(c, "ops", row["id"], 3)
        stats = learnings.beta_stats(c)
        assert (stats["saved"], stats["dropped"], stats["unreported"]) == (0, 0, 2)
        learnings.record_saved(c, "ops", row["id"], 1)
        assert c.execute("SELECT learnings_saved FROM learning_deliveries").fetchone()[0] == 1


def test_pending_expires_after_seven_days_without_writing_on_a_read_connection(api):
    store = api.app.state.store
    with store.transaction() as c:
        task = H.task_create(
            c, "human:ana", "Invoice rules", "Friday is the cutoff.", "bot:ops", lint=False, private=False
        )
        item = {"key": "rule", "source": "task", "ref": {"task": task["id"]}}
        old = keep_items(c, [item], H.shift(H.now(), days=-8))
        fresh = keep_items(c, [item], H.shift(H.now(), days=-6))
    with store.read() as c:
        c.execute("PRAGMA query_only=ON")
        try:
            assert [i["run_id"] for i in learnings.pending(c, "ops")] == [fresh]
            assert c.execute("SELECT expired_at FROM learning_deliveries WHERE run_id=?", (old,)).fetchone()[0] is None
        finally:
            c.execute("PRAGMA query_only=OFF")
    with store.transaction() as c:
        assert [i["run_id"] for i in learnings.pending(c, "ops")] == [fresh]
        assert c.execute("SELECT expired_at FROM learning_deliveries WHERE run_id=?", (old,)).fetchone()[0]
        counts = learnings.delivery_details(c, old)
        assert counts["expired"] == 1 and counts["not_sent"] == counts["unreported"] == 0
        detail = json.loads(c.execute("SELECT detail FROM learning_runs WHERE id=?", (old,)).fetchone()[0])
        assert detail["expired"] == learnings.beta_stats(c, days=30)["expired"] == 1
        row = queue(c)
        updates.send_request(c, row, delivery=True)
        assert c.execute("SELECT update_batch_id FROM learning_deliveries WHERE run_id=?", (old,)).fetchone()[0] is None
        c.execute("UPDATE learning_deliveries SET created_at=? WHERE run_id=?", (H.shift(H.now(), days=-8), fresh))
        assert [i["run_id"] for i in learnings.pending(c, "ops", row["id"])] == [fresh]
        assert c.execute("SELECT expired_at FROM learning_deliveries WHERE run_id=?", (fresh,)).fetchone()[0] is None


def test_pending_checks_at_most_one_hundred_candidates(api, monkeypatch):
    checked, original = [], learnings.shared_item

    def shared(c, item, bot_id):
        checked.append(item["item_key"])
        return original(c, item, bot_id)

    monkeypatch.setattr(learnings, "shared_item", shared)
    with api.app.state.store.transaction() as c:
        for run in range(7):
            keep_items(c, [{"key": f"gone:{run}:{n}", "source": "task", "ref": {"task": "gone"}} for n in range(15)])
        task = H.task_create(
            c, "human:ana", "Invoice rules", "Friday is the cutoff.", "bot:ops", lint=False, private=False
        )
        keep_items(c, [{"key": "valid", "source": "task", "ref": {"task": task["id"]}, "scores": {"ops": 0.8}}])
        assert learnings.pending(c, "ops") == []
        assert len(checked) == 100


def test_learnings_page_reads_pending_update_and_old_packet_receipts(api, monkeypatch):
    task, _ = scoring(api, monkeypatch, count=2)
    ctx = context(api)
    run_step(api, ctx)
    store = api.app.state.store
    with store.transaction() as c:
        c.execute(
            "INSERT INTO learning_runs(id,night,status,started_at) VALUES('old','2026-09-01','done',?)", (H.now(),)
        )
        c.execute(
            "INSERT INTO learning_deliveries(run_id,recipient,task_id,item_keys,created_at) "
            "VALUES('old','ops',?,'[]',?)",
            (task["id"], H.now()),
        )
    page = api.get(f"/api/v2/learnings/{ctx['run_id']}", headers=headers())
    assert page.status_code == 200 and page.json()["sections"]["bots"][0]["delivery_status"]
    assert api.get("/api/v2/learnings/old/packets/ops", headers=headers()).status_code == 200
    with store.transaction() as c:
        row = queue(c)
        updates.send_request(c, row, delivery=True)
        learnings.record_saved(c, "ops", row["id"], 1)
    packet = api.get(f"/api/v2/learnings/{ctx['run_id']}/packets/ops", headers=headers())
    assert packet.status_code == 200 and len(packet.json()["items"]) == 2


def test_learnings_night_counts_one_item_in_the_singular(api):
    store = api.app.state.store
    with store.transaction() as c:
        task = H.task_create(
            c, "human:ana", "Invoice rules", "Friday is the cutoff.", "bot:ops", lint=False, private=False
        )
        item = {"key": "rule", "source": "task", "ref": {"task": task["id"]}}
        old = keep_items(c, [item], H.shift(H.now(), days=-8))
        fresh = keep_items(c, [item])

    def status(run_id):
        page = api.get(f"/api/v2/learnings/{run_id}", headers=headers())
        assert page.status_code == 200
        return page.json()["sections"]["bots"][0]["delivery_status"]

    assert status(fresh) == "1 item waiting for Update"
    with store.transaction() as c:
        learnings.pending(c, "ops")  # expires the eight-day-old delivery
        updates.send_request(c, queue(c), delivery=True)
    assert status(old) == "1 item expired"
    assert status(fresh) == "1 item sent in Update"
