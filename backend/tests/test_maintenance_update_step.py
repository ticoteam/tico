"""The Update step (backend/maintenance_steps/update.py) and the update shape it asks for: the zero-work skip, close call D
(a delivery turn that writes no update is never asked again), the taskless main-lane request, private tasks kept out of
the request, bottom-up order with a manager waiting on its reports, the linter for version 2 only, purge_rejected
keeping old updates, the Friday weekly kind, the draft's spend under `update-draft`, and what a turn reports back."""
import importlib.util
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import pytest

from backend import execution, updates
from backend import models as M
from backend import run_triggers as RT
from backend.maintenance_steps import update as step
from backend.store import H
from backend.tests.test_api import api, post  # noqa: F401
from backend.tests.test_bot_contact import bot

TUESDAY = datetime(2026, 9, 29, 13, 0, tzinfo=timezone.utc)     # Tuesday 06:00 Pacific
FRIDAY = datetime(2026, 10, 2, 13, 0, tzinfo=timezone.utc)      # Friday 06:00 Pacific
EARLY = datetime(2026, 9, 29, 10, 0, tzinfo=timezone.utc)       # Tuesday 03:00 Pacific, before the cycle starts
DAY = "2026-09-29"


def ctx(api, now):
    return {"run_id": "run-test", "window_start": "", "window_end": "", "settings": api.app.state.store.settings,
            "now": now, "cap": 0.0}


def bid(c, slug):
    return H.resolve_bot(c, slug)["id"]


def states(c, day=DAY):
    return {r["bot"]: r["state"] for r in c.execute("SELECT bot, state FROM update_queue WHERE day=?", (day,))}


def turn(c, bot_id, writes=1, at=None, state="completed"):
    """A bot's settled turn (an `attempts` row), settled the way turn_finish settles one: did_nothing comes from its writes."""
    turn_id = H.new_id()
    c.execute("INSERT INTO attempts(id,bot_id,created_at,state,writes) VALUES(?,?,?,?,?)",
              (turn_id, bot_id, at or H.now(), state, writes))
    c.execute(f"UPDATE attempts SET settled_at=?,exit='ok',{RT.SETTLE_SQL} WHERE id=?", (H.now(), turn_id))
    return turn_id


def test_a_bot_that_did_no_work_since_its_last_update_gets_no_request(api):
    store = api.app.state.store
    bot(api, "idle", "Idle")
    with store.transaction() as c:
        idle, ops = bid(c, "idle"), bid(c, "ops")
        assert not step.did_work(c, idle, "")
        turn(c, ops)
        assert step.did_work(c, ops, "")
        # A keeper-room reply is an answer to a wake: a wake answered with "nothing new" is no work.
        c.execute("INSERT INTO messages(id,sender_id,recipient_id,kind,body,created_at) VALUES('wake-reply',?,?,'say',"
                  "'Nothing new.',?)", (H.bot_actor(idle), H.KEEPER, H.now()))
        assert not step.did_work(c, idle, ""), "a reply to the keeper is not work"
        result = step.run(c, ctx(api, TUESDAY))
        assert states(c)[idle] == "skipped", "the idle bot is skipped, not asked"
        assert c.execute("SELECT reason FROM update_queue WHERE bot=? AND day=?", (idle, DAY)).fetchone()["reason"] == updates.NO_WORK
        assert states(c)[ops] == "queued" and result["status"] == "pending" and result["details"]["asked"] >= 1
        assert step.plan_request(c, idle, DAY) == "skip"


def test_a_wake_answered_with_nothing_new_is_no_update_request_even_with_a_memory_commit(api):
    bot(api, "waker", "Waker")
    store = api.app.state.store
    with store.transaction() as c:
        waker = bid(c, "waker")
        turn(c, waker, writes=0)                                                     # the wake's turn: did_nothing = 1
        c.execute("INSERT INTO bot_memory_updates(bot,sha,subject,author,committed_at,files,runner_id,reported_at) "
                  "VALUES(?,?,?,?,?,?,?,?)", (waker, "5e1f0c2", "Rewrite state.md", "waker", H.now(), "state.md",
                                             "runner-test", H.now()))                 # the memory commit that turn made
        c.execute("INSERT INTO messages(id,sender_id,recipient_id,kind,body,created_at) VALUES('waker-reply',?,?,'say',"
                  "'Nothing new since yesterday.',?)", (H.bot_actor(waker), H.KEEPER, H.now()))
        assert not step.did_work(c, waker, ""), "a memory commit and a reply are not work"
        assert step.plan_request(c, waker, DAY) == "skip"
        step.run(c, ctx(api, TUESDAY))
        assert states(c)[waker] == "skipped", "no update request for a wake answered with nothing new"


def test_only_a_settled_turn_that_wrote_after_the_last_update_is_work(api):
    bot(api, "tally", "Tally")
    bot(api, "crashed", "Crashed")
    store = api.app.state.store
    with store.transaction() as c:
        tally, crashed = bid(c, "tally"), bid(c, "crashed")
        turn(c, tally, writes=0)
        assert not step.did_work(c, tally, ""), "a turn that did nothing is not work"
        turn(c, tally, writes=2, at=H.shift(H.now(), hours=-5))
        assert not step.did_work(c, tally, H.shift(H.now(), hours=-2)), "work before the bot's last update does not count"
        assert step.did_work(c, tally, H.shift(H.now(), hours=-6)), "a settled write after `since` is work"
        c.execute("INSERT INTO attempts(id,bot_id,created_at,state,writes,settled_at,exit,did_nothing) "
                  "VALUES(?,?,?,'failed',1,?,'error',0)", (H.new_id(), crashed, H.now(), H.now()))
        assert not step.did_work(c, crashed, ""), "a failed turn is not work"


def test_a_bot_with_only_learnings_waiting_gets_one_delivery_turn_that_writes_no_update_and_is_never_asked_again(api, monkeypatch):
    store = api.app.state.store
    bot(api, "reader", "Reader")
    monkeypatch.setattr(updates, "request_slots", lambda c, b: {"quiet_tasks": [], "learnings": ["Keep the Friday cut-off"],
                                                                 "suggestions": []})
    with store.transaction() as c:
        reader, ops = bid(c, "reader"), bid(c, "ops")
        step.run(c, ctx(api, TUESDAY))
        row = c.execute("SELECT * FROM update_queue WHERE bot=? AND day=?", (reader, DAY)).fetchone()
        assert row["state"] == "queued" and row["delivery_only"] == 1, "no work, but learnings waiting: a delivery turn"
        c.execute("UPDATE update_queue SET state='skipped' WHERE day=? AND bot<>?", (DAY, reader))   # only this one goes out
        assert updates.dispatch(c, TUESDAY) == reader
        sent = c.execute("SELECT q.*, m.refs, m.body FROM update_queue q JOIN messages m ON m.id=q.message_id WHERE q.id=?",
                         (row["id"],)).fetchone()
        assert json.loads(sent["refs"])["delivery_only"] is True and "Keep the Friday cut-off" in sent["body"]
        assert "do not post one now" in sent["body"]
        # Its run completes and it posts nothing: the turn ends delivered_without_update, never retried.
        c.execute("INSERT OR REPLACE INTO jobs(id,message_id,bot_id,state,created_at) VALUES(?,?,?,'completed',?)",
                  (H.new_id(), sent["message_id"], reader, H.now()))
        assert updates.settle_sent(c, dict(sent)) is False
        assert c.execute("SELECT state FROM update_queue WHERE id=?", (row["id"],)).fetchone()["state"] == "delivered_without_update"
        assert updates.dispatch(c, TUESDAY) is None
        assert c.execute("SELECT count(*) FROM update_queue WHERE bot=? AND day=?", (reader, DAY)).fetchone()[0] == 1


def test_an_ask_that_completes_without_an_update_is_asked_again(api):
    store = api.app.state.store
    with store.transaction() as c:
        ops = bid(c, "ops")
        turn(c, ops)
        step.run(c, ctx(api, TUESDAY))
        row = c.execute("SELECT * FROM update_queue WHERE bot=? AND day=?", (ops, DAY)).fetchone()
        assert row["delivery_only"] == 0
        message = updates.send_request(c, row)
        c.execute("INSERT OR REPLACE INTO jobs(id,message_id,bot_id,state,created_at) VALUES(?,?,?,'completed',?)",
                  (H.new_id(), message["id"], ops, H.now()))
        sent = c.execute("SELECT * FROM update_queue WHERE id=?", (row["id"],)).fetchone()
        assert updates.settle_sent(c, dict(sent)) is False
        assert c.execute("SELECT state FROM update_queue WHERE id=?", (row["id"],)).fetchone()["state"] != "delivered_without_update"


def test_the_request_is_a_taskless_notice_in_the_main_lane_path(api):
    store = api.app.state.store
    with store.transaction() as c:
        ops = bid(c, "ops")
        turn(c, ops)
        step.run(c, ctx(api, TUESDAY))
        c.execute("UPDATE update_queue SET state='skipped' WHERE day=? AND bot<>?", (DAY, ops))
        assert updates.dispatch(c, TUESDAY) == ops
        msg = c.execute("SELECT m.* FROM update_queue q JOIN messages m ON m.id=q.message_id WHERE q.bot=? AND q.day=?",
                        (ops, DAY)).fetchone()
        assert msg["sender_id"] == H.KEEPER and msg["kind"] == "notice" and msg["recipient_id"] == H.bot_actor(ops)
        conversation = c.execute("SELECT * FROM conversations WHERE id=?", (msg["conversation_id"],)).fetchone()
        message = {**dict(msg), "refs": json.loads(msg["refs"])}     # the decoded message the runner's rules read
        assert execution.work_task_id(message, dict(conversation)) is None, "no task: the notice takes no task lane"
        assert "task" not in message["refs"], "the request names no task"


def test_private_tasks_stay_out_of_the_request_and_shared_ones_go_in(api):
    post(api, "tasks", {"owner": "bot:ops", "title": "Ship the pricing page", "body": "x"})
    post(api, "tasks", {"owner": "bot:ops", "title": "Secret raise plan", "body": "x", "private": True})
    store = api.app.state.store
    with store.transaction() as c:
        text = updates.request_text(c, bid(c, "ops"), "daily", DAY)
    assert "Ship the pricing page" in text
    assert "Secret raise plan" not in text


def test_bottom_up_order_defers_a_manager_until_its_report_has_posted(api):
    bot(api, "boss", "Manager", reports_to="human:ana")
    bot(api, "analyst", "Analyst", reports_to="boss")
    store = api.app.state.store
    with store.transaction() as c:
        analyst, boss = bid(c, "analyst"), bid(c, "boss")
        turn(c, analyst)
        step.run(c, ctx(api, TUESDAY))
        assert states(c)[boss] == "queued", "a manager is asked: its report will post today"
        c.execute("UPDATE update_queue SET state='skipped' WHERE day=? AND bot NOT IN (?,?)", (DAY, analyst, boss))
        assert updates.dispatch(c, TUESDAY) == analyst, "the report is asked first"
        assert updates.dispatch(c, TUESDAY) is None, "one request out at a time: the manager waits"
        updates.post(c, analyst, "- Sized the market", day=DAY)
        assert updates.dispatch(c, TUESDAY) == boss, "the manager is asked once its report has posted"


def test_a_manager_whose_reports_have_nothing_to_do_gets_no_request(api):
    bot(api, "boss2", "Manager", reports_to="human:ana")
    bot(api, "quiet-report", "Quiet", reports_to="boss2")
    store = api.app.state.store
    with store.transaction() as c:
        boss, report = bid(c, "boss2"), bid(c, "quiet-report")
        step.run(c, ctx(api, TUESDAY))
        assert states(c)[report] == "skipped" and states(c)[boss] == "skipped", "neither has work: no roll-up is asked"


def test_the_linter_checks_version_two_only(api):
    four = "- a one\n- b two\n- c three\n- d four"
    assert updates.lint(four, "daily", 1) is None, "an update of the old shape is not linted by these rules"
    assert "4 bullets" in updates.lint(four, "daily", 2) and "4 bullets" in updates.lint(four, "daily")
    flags = "- one\n- two\n- three\n- Blocked: the vendor is late\n- Suggestion: cut the weekly report"
    assert updates.lint(flags, "daily", 2) is None, "three bullets of work and one blocker and one suggestion pass"
    assert "section label" in updates.lint("- Done: shipped\n- b\n- c", "daily", 2)
    many = "- a\n- b\n- c\n- Blocked: x\n- Blocked: y\n- Blocked: z\n- Blocked: w"
    assert "4 blocked bullets" in updates.lint(many, "daily", 2)


def test_purge_rejected_keeps_old_updates_and_deletes_new_ones_that_break_the_shape(api, monkeypatch):
    monkeypatch.setattr(H, "STYLE_LINT", "refuse")
    five = "- one\n- two\n- three\n- four\n- five"
    store = api.app.state.store
    with store.transaction() as c:
        ops, finance = bid(c, "ops"), bid(c, "finance")
        c.execute("INSERT INTO updates(id,bot,kind,day,headline,body,created_at,updated_at) "
                  "VALUES('old-shape',?,'daily','2026-09-28','one',?,?,?)", (ops, five, H.now(), H.now()))
        c.execute("INSERT INTO updates(id,bot,kind,day,headline,body,created_at,updated_at,format_version) "
                  "VALUES('new-shape',?,'daily','2026-09-27','one',?,?,?,2)", (finance, five, H.now(), H.now()))
        removed = updates.purge_rejected(c)
        assert c.execute("SELECT format_version FROM updates WHERE id='old-shape'").fetchone()[0] == 1, \
            "a version 1 update survives the purge"
        assert c.execute("SELECT 1 FROM updates WHERE id='new-shape'").fetchone() is None
        assert finance in removed


def test_a_friday_is_the_weekly_kind(api):
    store = api.app.state.store
    with store.transaction() as c:
        ops = bid(c, "ops")
        turn(c, ops)
        result = step.run(c, ctx(api, FRIDAY))
        kinds = {r["kind"] for r in c.execute("SELECT kind FROM update_queue WHERE day='2026-10-02'")}
        assert kinds == {"weekly"} and result["status"] == "pending"


def test_the_draft_is_metered_into_the_decision_spend_as_update_draft(api):
    store = api.app.state.store
    with store.transaction() as c:
        c.execute("INSERT INTO update_queue(id,bot,kind,day,rank,state,sent_at) "
                  "VALUES('draft-row','human:ana','daily',?,0,'sent',?)", (DAY, H.now()))

    def write(system, user):
        return {"body": "- Shipped the pricing page"}
    write.model = "acme-draft-model"
    updates.write_draft(store, "human:ana", write, "coo")
    with store.read() as c:
        spent = c.execute("SELECT target, detail FROM events WHERE action='judge.spend'").fetchall()
    assert [r["target"] for r in spent] == ["update-draft"]
    assert json.loads(spent[0]["detail"])["model"] == "acme-draft-model"


def test_a_turn_reports_what_it_kept_and_acted_on(api):
    store = api.app.state.store
    with store.transaction() as c:
        ops = bid(c, "ops")
        uid = updates.post(c, ops, "- Did the thing", day=DAY, learnings_saved=2, suggestions_seen=["Trim the report"],
                           delivery_batch_id="batch-1")["id"]
        row = c.execute("SELECT format_version, learnings_saved, suggestions_seen, delivery_batch_id FROM updates WHERE id=?",
                        (uid,)).fetchone()
        assert (row["format_version"], row["learnings_saved"], row["delivery_batch_id"]) == (2, 2, "batch-1")
        card = updates.one(c, uid)
        assert card["suggestions_seen"] == ["Trim the report"] and "format_version" not in card


def test_the_api_model_bounds_what_a_turn_reports(api):
    ok = M.UpdatePost(body="- a", learnings_saved=15, suggestions_seen=["x"], delivery_batch_id="batch-1")
    assert ok.learnings_saved == 15
    with pytest.raises(Exception):
        M.UpdatePost(body="- a", learnings_saved=16)


def _migration(name):
    spec = importlib.util.spec_from_file_location(name, Path(__file__).resolve().parents[1] / "migrations" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_migration_0079_adds_the_columns_once_and_marks_old_updates_version_one():
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE updates(id TEXT PRIMARY KEY, bot TEXT, kind TEXT, day TEXT, headline TEXT, body TEXT, "
                 "created_at TEXT, updated_at TEXT)")
    conn.execute("CREATE TABLE update_queue(id TEXT PRIMARY KEY, bot TEXT, kind TEXT, day TEXT, rank INTEGER, state TEXT)")
    conn.execute("INSERT INTO updates VALUES('u','ops','daily','2026-09-28','h','- one','t','t')")
    migration = _migration("0079_update_format_and_delivery")
    migration.up(conn)
    migration.up(conn)
    cols = {r[1] for r in conn.execute("PRAGMA table_info(updates)")}
    assert {"format_version", "learnings_saved", "suggestions_seen", "delivery_batch_id"} <= cols
    assert conn.execute("SELECT format_version FROM updates").fetchone()[0] == 1
    assert "delivery_only" in {r[1] for r in conn.execute("PRAGMA table_info(update_queue)")}
