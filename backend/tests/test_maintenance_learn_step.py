"""Learn keeps bounded items for Update, with source checks, spend and delivery receipts."""

import importlib.util
import json
from pathlib import Path
import sqlite3

import pytest

from backend import judge, learnings, updates
from backend.maintenance_steps import learn as step
from backend.maintenance_steps import update as update_step
from backend.store import H
from backend.tests.test_api import api  # noqa: F401
from backend.tests.test_bot_contact import bot


def context(api, **changes):
    now = H.now()
    ctx = {"run_id": H.new_id(), "window_start": H.shift(now, days=-1), "window_end": now, "now": now,
           "settings": {"key": "learn", "enabled": 1, "frequency": "day"}, "cap": 0.0,
           "store": api.app.state.store, "auth": api.app.state.auth}
    return {**ctx, **changes}


def run_step(api, ctx=None):
    store = api.app.state.store
    with store.read() as c:
        c.execute("PRAGMA query_only=ON")
        try:
            return step.run(c, ctx or context(api))
        finally:
            c.execute("PRAGMA query_only=OFF")


def scoring(api, monkeypatch, count=20, recipients=("ops",)):
    store = api.app.state.store
    with store.transaction() as c:
        task = H.task_create(c, "human:ana", "Invoice rules", "Friday is the cutoff.", "bot:ops", lint=False, private=False)
    items = [{"key": f"task:{n}", "source": "task", "author": "human:ana", "ref": {"task": task["id"]},
              "created_at": H.now(), "label": f"Rule {n}", "text": f"Keep rule {n}.\nIts source is explicit.",
              "audience": None} for n in range(count)]
    team = {recipient: {"name": recipient, "profile": recipient} for recipient in recipients}
    calls = []

    def engine(state, questions, label=None):
        calls.append(state)
        value = 0.6 + int(state["item"]["label"].split()[-1]) / 1000
        return {"answers": {key: {"type": "noul", "noul": value} for key in questions}, "cost_usd": 0.002}

    monkeypatch.setattr(learnings, "engine_for", lambda *args: engine)
    monkeypatch.setattr(learnings, "roster", lambda *args: team)
    monkeypatch.setattr(learnings, "collect", lambda *args: ([dict(i) for i in items], 0))
    monkeypatch.setattr(learnings, "question_set", lambda *args: {
        "label": "learnings-route", "questions": {"learnable": {"type": "noul", "instructions": "Durable?"}},
        "thresholds": {"noise": 0.2, "relevant": 0.6, "per_recipient": 99}})
    return task, calls


def queue(c, bot_id="ops", day=None):
    day = day or updates.today()
    updates.build_queue(c, day, updates.kind_for(day))
    return c.execute("SELECT * FROM update_queue WHERE bot=? AND day=?", (bot_id, day)).fetchone()


def complete(c, row, text):
    job, attempt = H.new_id(), H.new_id()
    c.execute("INSERT OR REPLACE INTO jobs(id,message_id,bot_id,state,attempt_id,created_at) VALUES(?,?,?,'completed',?,?)",
              (job, row["message_id"], row["bot"], attempt, H.now()))
    c.execute("INSERT INTO attempts(id,job_id,bot_id,state,created_at,final_text) VALUES(?,?,?,'completed',?,?)",
              (attempt, job, row["bot"], H.now(), text))
    return attempt


def test_off_and_exhausted_cap_make_no_model_calls_or_deliveries(api, monkeypatch):
    _, calls = scoring(api, monkeypatch)
    assert run_step(api, context(api, settings={"enabled": 0}))["status"] == "skipped"
    store = api.app.state.store
    with store.transaction() as c:
        H.event(c, H.KEEPER, "judge.spend", "earlier", {"cost_usd": 1.0})
    result = run_step(api, context(api, cap=1.0))
    assert result["status"] == "skipped" and "cap" in result["reason"]
    assert result["cost_usd"] == 0 and calls == []
    with store.read() as c:
        assert c.execute("SELECT count(*) FROM learning_deliveries").fetchone()[0] == 0
        assert c.execute("SELECT count(*) FROM learning_cursors").fetchone()[0] == 0


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


def test_each_call_reserves_against_the_context_cap_in_the_judge_budget(api, monkeypatch):
    scoring(api, monkeypatch, count=3)
    paid = []
    monkeypatch.setattr(judge, "reservation", lambda *args: 0.6)

    def engine_for(store, c):
        def engine(state, questions, label=None):
            def call():
                paid.append(state)
                return {"answers": {key: {"type": "noul", "noul": 0.9} for key in questions},
                        "usage": {"cost_usd": 0.6}}
            return judge.metered(store, [], state, questions, label, call)
        return engine

    monkeypatch.setattr(learnings, "engine_for", engine_for)
    result = run_step(api, context(api, cap=1.0))
    assert result["status"] == "skipped" and len(paid) == 1
    assert result["cost_usd"] == pytest.approx(0.6)
    with api.app.state.store.read() as c:
        assert judge.spent_this_month(c) == pytest.approx(0.6)
        assert c.execute("SELECT count(*) FROM learning_deliveries").fetchone()[0] == 0


def test_update_sends_fifteen_across_runs_and_binds_the_exact_batch_once(api, monkeypatch):
    scoring(api, monkeypatch)
    store = api.app.state.store
    run_step(api)
    run_step(api)                 # A custom frequency can leave several pending runs on one day.
    with store.transaction() as c:
        assert update_step.plan_request(c, "ops", updates.today()) == "deliver"
        row = queue(c)
        message = updates.send_request(c, row, delivery=True)
        assert "Learnings for you:" in message["body"] and row["id"] in message["body"]
        assert len([line for line in message["body"].splitlines() if line.startswith("- Rule ")]) == 15
        rows = c.execute("SELECT * FROM learning_deliveries WHERE recipient='ops'").fetchall()
        assert {r["update_batch_id"] for r in rows} == {row["id"]}
        assert sum(len(json.loads(r["sent_item_keys"])) for r in rows) == 15
        assert updates.request_slots(c, "ops")["learnings"] == []
        retry = updates.send_request(c, row, delivery=True)
        assert retry["body"] == message["body"]
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


def test_completed_delivery_parses_saved_count_and_logs_stats_once(api, monkeypatch):
    scoring(api, monkeypatch, count=4)
    store, ctx = api.app.state.store, context(api)
    run_step(api, ctx)
    with store.transaction() as c:
        row = queue(c)
        updates.send_request(c, row, delivery=True)
        sent = c.execute("SELECT * FROM update_queue WHERE id=?", (row["id"],)).fetchone()
        complete(c, sent, "Kept two rules.\nlearnings_saved: 2")
        assert updates.settle_sent(c, sent) is False
        assert c.execute("SELECT state FROM update_queue WHERE id=?", (row["id"],)).fetchone()[0] == "delivered_without_update"
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
        updates.post(c, "ops", "- Finished invoices", day=row["day"], learnings_saved=3, delivery_batch_id="other-batch")
        assert c.execute("SELECT learnings_saved FROM learning_deliveries").fetchone()[0] is None
        updates.post(c, "ops", "- Finished invoices", day=row["day"], learnings_saved=1, delivery_batch_id=row["id"])
        sent = c.execute("SELECT * FROM update_queue WHERE id=?", (row["id"],)).fetchone()
        assert updates.settle_sent(c, sent) is False
        assert c.execute("SELECT learnings_saved FROM learning_deliveries").fetchone()[0] == 1


def test_final_feedback_after_a_post_is_collected_by_the_next_queue_tick(api, monkeypatch):
    scoring(api, monkeypatch, count=2)
    run_step(api)
    with api.app.state.store.transaction() as c:
        row = queue(c, day="2026-11-04")
        updates.send_request(c, row)
        updates.post(c, "ops", "- Finished invoices", day=row["day"])
        sent = c.execute("SELECT * FROM update_queue WHERE id=?", (row["id"],)).fetchone()
        updates.settle_sent(c, sent)
        complete(c, sent, "learnings_saved: 1")
        updates.settle_learnings(c)
        assert c.execute("SELECT learnings_saved FROM learning_deliveries").fetchone()[0] == 1


def test_missing_or_out_of_range_feedback_is_unreported(api, monkeypatch):
    scoring(api, monkeypatch, count=2)
    run_step(api)
    with api.app.state.store.transaction() as c:
        row = queue(c)
        updates.send_request(c, row, delivery=True)
        sent = c.execute("SELECT * FROM update_queue WHERE id=?", (row["id"],)).fetchone()
        complete(c, sent, "Nothing new.")
        updates.settle_sent(c, sent)
        learnings.record_saved(c, "ops", row["id"], 3)
        stats = learnings.beta_stats(c)
        assert (stats["saved"], stats["dropped"], stats["unreported"]) == (0, 0, 2)
        learnings.record_saved(c, "ops", row["id"], 1)
        assert c.execute("SELECT learnings_saved FROM learning_deliveries").fetchone()[0] == 1


def test_learnings_page_reads_pending_update_and_old_packet_receipts(api, monkeypatch):
    task, _ = scoring(api, monkeypatch, count=2)
    ctx = context(api)
    run_step(api, ctx)
    store = api.app.state.store
    with store.transaction() as c:
        c.execute("INSERT INTO learning_runs(id,night,status,started_at) VALUES('old','2026-09-01','done',?)", (H.now(),))
        c.execute("INSERT INTO learning_deliveries(run_id,recipient,task_id,item_keys,created_at) "
                  "VALUES('old','ops',?,'[]',?)", (task["id"], H.now()))
    page = api.get(f"/api/v2/learnings/{ctx['run_id']}")
    assert page.status_code == 200 and page.json()["sections"]["bots"][0]["delivery_status"]
    assert api.get("/api/v2/learnings/old/packets/ops").status_code == 200
    with store.transaction() as c:
        row = queue(c)
        updates.send_request(c, row, delivery=True)
        learnings.record_saved(c, "ops", row["id"], 1)
    packet = api.get(f"/api/v2/learnings/{ctx['run_id']}/packets/ops")
    assert packet.status_code == 200 and len(packet.json()["items"]) == 2


def test_same_day_runs_are_not_lost_between_pages_and_stats_cover_seven_days(api, monkeypatch):
    scoring(api, monkeypatch, count=2)
    run_step(api)
    run_step(api)
    store = api.app.state.store
    with store.transaction() as c:
        old = H.shift(H.now(), days=-8)
        c.execute("INSERT INTO learning_runs(id,night,status,started_at,read_count,detail) "
                  "VALUES('old',?,'quiet',?,99,?)", (old[:10], old, json.dumps({"cost_usd": 99.0})))
        stats = learnings.beta_stats(c)
        assert stats["days"] == 7 and stats["runs"] == 2 and stats["items_read"] == 4
        assert stats["cost_usd"] == pytest.approx(0.008)
    first = api.get("/api/v2/learnings", params={"limit": 1}).json()
    second = api.get("/api/v2/learnings", params={"limit": 1, "before": first["next_before"]}).json()
    assert first["nights"][0]["night"] == second["nights"][0]["night"]
    assert first["nights"][0]["id"] != second["nights"][0]["id"]


def test_migration_0085_is_idempotent_and_keeps_old_rows_and_indexes():
    path = Path(__file__).resolve().parents[1] / "migrations" / "0085_learn_beta.py"
    spec = importlib.util.spec_from_file_location("learn_beta", path)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    with sqlite3.connect(":memory:") as c:
        c.execute("CREATE TABLE learning_deliveries(run_id TEXT NOT NULL,recipient TEXT NOT NULL,task_id TEXT NOT NULL, "
                  "item_keys TEXT NOT NULL,created_at TEXT NOT NULL,PRIMARY KEY(run_id,recipient))")
        c.execute("CREATE TABLE learning_runs(id TEXT PRIMARY KEY,night TEXT NOT NULL UNIQUE,status TEXT NOT NULL, "
                  "started_at TEXT NOT NULL,finished_at TEXT,read_count INTEGER NOT NULL DEFAULT 0, "
                  "routed_count INTEGER NOT NULL DEFAULT 0,detail TEXT NOT NULL DEFAULT '{}')")
        c.execute("INSERT INTO learning_runs(id,night,status,started_at) VALUES('old','same-day','done','then')")
        c.execute("CREATE INDEX old_learning_tasks ON learning_deliveries(task_id)")
        c.execute("CREATE VIEW old_packets AS SELECT task_id FROM learning_deliveries")
        c.execute("INSERT INTO learning_deliveries VALUES('old','ops','packet-task','[\"item\"]','then')")
        migration.up(c)
        migration.up(c)
        row = c.execute("SELECT task_id,item_keys,update_batch_id FROM learning_deliveries WHERE run_id='old'").fetchone()
        assert row == ("packet-task", '["item"]', None)
        assert c.execute("SELECT night FROM learning_runs WHERE id='old'").fetchone()[0] == "same-day"
        c.execute("INSERT INTO learning_runs(id,night,status,started_at) VALUES('again','same-day','quiet','now')")
        assert c.execute("SELECT 1 FROM sqlite_master WHERE name='old_learning_tasks'").fetchone()
        assert c.execute("SELECT task_id FROM old_packets WHERE task_id IS NOT NULL").fetchone()[0] == "packet-task"
        c.execute("INSERT INTO learning_deliveries(run_id,recipient,item_keys,created_at) VALUES('new','ops','[]','now')")
        assert c.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    with sqlite3.connect(":memory:") as c:
        migration.up(c)
        migration.up(c)
        c.execute("INSERT INTO learning_runs(id,night,status,started_at) VALUES('one','same-day','done','now')")
        c.execute("INSERT INTO learning_runs(id,night,status,started_at) VALUES('two','same-day','done','now')")
