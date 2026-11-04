"""Learn keeps bounded items for Update, with source checks, spend and delivery receipts."""

import importlib.util
import json
from pathlib import Path
import sqlite3

import pytest

from backend import judge, learnings, slack_gateway, updates
from backend.maintenance_steps import learn as step
from backend.maintenance_steps import update as update_step
from backend.store import H
from backend.tests.test_api import api, assign, claim, headers, post, ready, runner  # noqa: F401
from backend.tests.test_bot_contact import bot
from backend.tests.test_mail_api import put_mail


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


def start(api, row):
    machine = runner(api)
    assign(api, machine, row["bot"])
    ready(api, machine, [row["bot"]])
    with api.app.state.store.transaction() as c:
        # Creation wakes are over; claim the Update request itself.
        c.execute("UPDATE jobs SET state='completed' WHERE bot_id=? AND message_id<>?", (row["bot"], row["message_id"]))
    attempt = claim(api, machine, row["bot"])
    assert attempt is not None
    with api.app.state.store.read() as c:
        job = c.execute("SELECT message_id FROM jobs WHERE attempt_id=?", (attempt["id"],)).fetchone()
        assert job[0] == row["message_id"]
    post(api, f"attempts/{attempt['id']}/started", {"thread_id": "update-turn"}, machine["token"])
    return machine, attempt


def complete(api, turn, text):
    machine, attempt = turn
    post(api, f"attempts/{attempt['id']}/complete", {"outcome": "completed", "text": text, "last_seq": 0}, machine["token"])


def keep_items(c, items, created_at=None):
    now, run_id = created_at or H.now(), H.new_id()
    c.execute("INSERT INTO learning_runs(id,night,status,started_at) VALUES(?,?,'done',?)", (run_id, now[:10], now))
    items = [{"author": "human:ana", "created_at": now, "label": item["key"], "text": "Keep this rule.",
              "learnable": 0.9, "scores": {"ops": 0.9}, **item} for item in items]
    learnings.dispatch(c, run_id, now[:10], items, {"ops": items}, {}, {})
    c.execute("UPDATE learning_deliveries SET created_at=? WHERE run_id=?", (now, run_id))
    return run_id


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


def test_only_known_public_slack_messages_reach_company_update(api):
    with api.app.state.store.transaction() as c:
        items = []
        for channel, channel_type in (("C_PUBLIC", "channel"), ("C_PRIVATE", "group"), ("C_UNKNOWN", None)):
            event, why = slack_gateway.event_from({"event_id": channel, "team_id": "T_ACME", "event": {
                "type": "message" if channel_type else "app_mention", "channel_type": channel_type,
                "channel": channel, "ts": "1.0", "user": "U_ANA", "text": "Keep this rule."}}, "U_TICO")
            assert why is None and event["channel_kind"] == "channel"
            c.execute("INSERT INTO slack_events(event_id,team_id,channel,channel_kind,thread_ts,ts,user_id,event_type,"
                      "text,received_at) VALUES(?,?,?,'channel','1.0','1.0','U_ANA',?,'Keep this rule.',?)",
                      (channel, "T_ACME", channel, event["event_type"], H.now()))
            items.append({"key": channel, "source": "slack", "ref": {"channel": channel, "ts": "1.0"}})
        keep_items(c, items)
        assert [i["item_key"] for i in learnings.pending(c, "ops")] == ["C_PUBLIC"]


def test_personal_sent_mail_stays_out_of_company_update(api):
    mail = put_mail(api, "ana@acme.example", from_addr="ana@acme.example", labels=["SENT"], body="Personal salary plans.")
    with api.app.state.store.transaction() as c:
        keep_items(c, [{"key": "personal-mail", "source": "mail",
                        "ref": {"mailbox": "ana@acme.example", "msg_id": mail["msg_id"]}}])
        assert updates.request_slots(c, "ops")["learnings"] == []


def test_real_public_task_comment_passes_provenance_but_private_comment_does_not(api):
    with api.app.state.store.transaction() as c:
        items = []
        for private in (False, True):
            title = "Private invoice rules" if private else "Public invoice rules"
            task = H.task_create(c, "human:ana", title, "Friday is the cutoff.", "bot:ops", lint=False, private=private)
            comment = H.task_comment(c, "human:ana", task["id"], "Keep the Friday cutoff.", wake=False)
            assert H.message(c, comment["id"])["restricted"] == 1
            items.append({"key": "private" if private else "public", "source": "task",
                          "ref": {"message": comment["id"]}})
        keep_items(c, items)
        assert [i["item_key"] for i in learnings.pending(c, "ops")] == ["public"]
        c.execute("UPDATE messages SET deleted_at=? WHERE id=?", (H.now(), items[0]["ref"]["message"]))
        assert learnings.pending(c, "ops") == []


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


@pytest.mark.parametrize("resend", [False, True])
def test_redo_keeps_the_original_delivery_turn_for_feedback_and_history(api, monkeypatch, resend):
    scoring(api, monkeypatch, count=2)
    run_step(api)
    store = api.app.state.store
    with store.transaction() as c:
        row = queue(c)
        updates.send_request(c, row, delivery=True)
        sent = c.execute("SELECT * FROM update_queue WHERE id=?", (row["id"],)).fetchone()
    turn = start(api, sent)
    with store.transaction() as c:
        updates.redo(c, row["kind"], row["day"], bots=["ops"])
        redo = c.execute("SELECT * FROM update_queue WHERE id=?", (row["id"],)).fetchone()
        assert redo["message_id"] is None
        if resend:
            assert updates.send_request(c, redo)["id"] != sent["message_id"]
        assert c.execute("SELECT message_id FROM learning_deliveries").fetchone()[0] == sent["message_id"]
    complete(api, turn, "learnings_saved: 1")
    with store.transaction() as c:
        updates.settle_learnings(c)
        assert c.execute("SELECT learnings_saved FROM learning_deliveries").fetchone()[0] == 1
        run_id = c.execute("SELECT run_id FROM learning_deliveries").fetchone()[0]
        assert learnings.delivery_details(c, run_id)["saved"] == 1
        delivery = c.execute("SELECT * FROM learning_deliveries").fetchone()
        assert learnings.delivery_attempts(c, delivery) == [turn[1]["id"]]
        c.execute("DELETE FROM update_queue WHERE id=?", (row["id"],))
        assert learnings.delivery_attempts(c, delivery) == [turn[1]["id"]]


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
        task = H.task_create(c, "human:ana", "Invoice rules", "Friday is the cutoff.", "bot:ops", lint=False, private=False)
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
        task = H.task_create(c, "human:ana", "Invoice rules", "Friday is the cutoff.", "bot:ops", lint=False, private=False)
        keep_items(c, [{"key": "valid", "source": "task", "ref": {"task": task["id"]}, "scores": {"ops": 0.8}}])
        assert learnings.pending(c, "ops") == []
        assert len(checked) == 100


def test_learnings_page_reads_pending_update_and_old_packet_receipts(api, monkeypatch):
    task, _ = scoring(api, monkeypatch, count=2)
    ctx = context(api)
    run_step(api, ctx)
    store = api.app.state.store
    with store.transaction() as c:
        c.execute("INSERT INTO learning_runs(id,night,status,started_at) VALUES('old','2026-09-01','done',?)", (H.now(),))
        c.execute("INSERT INTO learning_deliveries(run_id,recipient,task_id,item_keys,created_at) "
                  "VALUES('old','ops',?,'[]',?)", (task["id"], H.now()))
    page = api.get(f"/api/v2/learnings/{ctx['run_id']}", headers=headers())
    assert page.status_code == 200 and page.json()["sections"]["bots"][0]["delivery_status"]
    assert api.get("/api/v2/learnings/old/packets/ops", headers=headers()).status_code == 200
    with store.transaction() as c:
        row = queue(c)
        updates.send_request(c, row, delivery=True)
        learnings.record_saved(c, "ops", row["id"], 1)
    packet = api.get(f"/api/v2/learnings/{ctx['run_id']}/packets/ops", headers=headers())
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
    first = api.get("/api/v2/learnings", params={"limit": 1}, headers=headers())
    assert first.status_code == 200, first.text
    first = first.json()
    second = api.get("/api/v2/learnings", params={"limit": 1, "before": first["next_before"]}, headers=headers())
    assert second.status_code == 200, second.text
    second = second.json()
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
        row = c.execute("SELECT task_id,item_keys,update_batch_id,message_id,expired_at "
                        "FROM learning_deliveries WHERE run_id='old'").fetchone()
        assert row == ("packet-task", '["item"]', None, None, None)
        assert c.execute("SELECT night FROM learning_runs WHERE id='old'").fetchone()[0] == "same-day"
        c.execute("INSERT INTO learning_runs(id,night,status,started_at) VALUES('again','same-day','quiet','now')")
        assert c.execute("SELECT 1 FROM sqlite_master WHERE name='old_learning_tasks'").fetchone()
        assert c.execute("SELECT 1 FROM sqlite_master WHERE name='learning_deliveries_expiry'").fetchone()
        assert c.execute("SELECT task_id FROM old_packets WHERE task_id IS NOT NULL").fetchone()[0] == "packet-task"
        c.execute("INSERT INTO learning_deliveries(run_id,recipient,item_keys,created_at) VALUES('new','ops','[]','now')")
        assert c.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    with sqlite3.connect(":memory:") as c:
        migration.up(c)
        migration.up(c)
        c.execute("INSERT INTO learning_runs(id,night,status,started_at) VALUES('one','same-day','done','now')")
        c.execute("INSERT INTO learning_runs(id,night,status,started_at) VALUES('two','same-day','done','now')")
