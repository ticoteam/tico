"""The Update step (backend/maintenance_steps/update.py) and the update shape it asks for: the zero-work skip, close call D
(a delivery turn that writes no update is never asked again), the taskless main-lane request, private tasks kept out of
the request, bottom-up order with a manager waiting on its reports, the wake limiter (a refused bot stays queued), the
linter for version 2 only, purge_rejected keeping old updates, the Friday weekly kind, the draft's spend under
`update-draft`, and what a turn reports back."""
import importlib.util
import io
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import pytest

from backend import execution, maintenance, maintenance_limits, updates
from backend import models as M
from backend import run_triggers as RT
from backend.maintenance_steps import update as step
from backend.scheduler import Scheduler
from backend.store import H
from clients.judge import gateway_writer, llm_writer
from backend.tests.test_api import api, post  # noqa: F401
from backend.tests.test_bot_contact import bot

TUESDAY = datetime(2026, 9, 29, 13, 0, tzinfo=timezone.utc)     # Tuesday 06:00 Pacific
FRIDAY = datetime(2026, 10, 2, 13, 0, tzinfo=timezone.utc)      # Friday 06:00 Pacific
EARLY = datetime(2026, 9, 29, 10, 0, tzinfo=timezone.utc)       # Tuesday 03:00 Pacific, before the cycle starts
DAY = "2026-09-29"


def ctx(api, now):
    """PR1's ctx: `settings` is the step's own row, the app's settings are on the store, and `now` is an H.now() string."""
    stamp = now.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f") + "Z"
    return {"run_id": "run-test", "window_start": "", "window_end": "", "now": stamp, "cap": 0.0,
            "settings": {"key": "update", "enabled": 1, "frequency": "day"}, "store": api.app.state.store, "auth": None}


def run_step(api, now=TUESDAY):
    """PR1's runner holds a read connection around the step, and the step writes on its own transaction: so no
    transaction is open when this runs."""
    with api.app.state.store.read() as c:
        return step.run(c, ctx(api, now))


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


@pytest.mark.parametrize("frequency,every,unit", [("week", None, None), ("custom", 24, "hours")])
def test_an_early_update_skip_does_not_use_up_its_cadence(api, monkeypatch, frequency, every, unit):
    store, auth = api.app.state.store, api.app.state.auth
    now = ctx(api, EARLY)["now"]
    monkeypatch.setattr(H, "now", lambda: now)
    monkeypatch.setattr(updates, "writer_for", lambda *args: None)
    with store.transaction() as c:
        c.execute("UPDATE maintenance_steps SET enabled=0 WHERE key NOT IN ('collect','update')")
        c.execute("UPDATE maintenance_steps SET frequency=?, custom_every=?, custom_unit=? WHERE key='update'",
                  (frequency, every, unit))
        ops = bid(c, "ops")
        turn(c, ops)
    for only in (None, "update"):
        run, _ = maintenance.run_now(store, auth, "human:ana", step=only, wait=True)
        logged = next(s for s in run["steps"] if s["step"] == "update")
        assert logged["status"] == "skipped" and "05:00" in logged["reason"], run
        with store.read() as c:
            assert c.execute("SELECT last_started_at FROM maintenance_steps WHERE key='update'").fetchone()[0] is None
            assert c.execute("SELECT cursor_at FROM maintenance_steps WHERE key='update'").fetchone()[0] is None
            assert not c.execute("SELECT 1 FROM update_queue").fetchone(), "no cycle before 05:00 company time"
    now = ctx(api, TUESDAY.replace(hour=12))["now"]     # exactly 05:00 Pacific
    run, _ = maintenance.run_now(store, auth, "human:ana", wait=True)
    logged = next(s for s in run["steps"] if s["step"] == "update")
    assert logged["status"] == "pending" and logged["details"]["built"] > 0, run
    with store.read() as c:
        queued = {r["id"] for r in c.execute("SELECT id FROM update_queue WHERE day=?", (DAY,))}
        assert states(c)[ops] == "queued"
        assert c.execute("SELECT last_started_at FROM maintenance_steps WHERE key='update'").fetchone()[0] == now
    again, _ = maintenance.run_now(store, auth, "human:ana", wait=True)
    logged = next(s for s in again["steps"] if s["step"] == "update")
    assert logged["status"] == "skipped" and logged["reason"] == "not due", again
    forced, _ = maintenance.run_now(store, auth, "human:ana", step="update", wait=True)
    assert forced["steps"][0]["details"]["built"] == 0, "Run now for Update keeps the existing day's queue"
    with store.read() as c:
        assert {r["id"] for r in c.execute("SELECT id FROM update_queue WHERE day=?", (DAY,))} == queued


def test_the_scheduler_only_pumps_maintenances_cycle_and_completes_its_original_step(api, monkeypatch):
    store = api.app.state.store
    now = ctx(api, TUESDAY)["now"]
    monkeypatch.setattr(H, "now", lambda: now)
    monkeypatch.setattr(updates, "writer_for", lambda *args: None)
    with store.transaction() as c:
        maintenance.save_settings(c, "human:ana", paused=True)
        ops = bid(c, "ops")
        turn(c, ops)
    scheduler = Scheduler(store, api.app.state.execution)
    assert not scheduler.tick(TUESDAY)["failures"]
    with store.read() as c:
        assert not c.execute("SELECT 1 FROM update_queue").fetchone(), "05:00 no longer starts a cycle on its own"
    run, started = maintenance.run_now(store, api.app.state.auth, "human:ana", step="update", wait=True)
    assert started and run["steps"][0]["status"] == "pending", run
    with store.read() as c:
        assert states(c)[ops] == "queued", "the step builds; the scheduler sends"
        assert c.execute("SELECT cursor_at FROM maintenance_steps WHERE key='update'").fetchone()[0] is None
        queued = c.execute("SELECT count(*) FROM update_queue").fetchone()[0]
    assert not scheduler.tick(TUESDAY)["failures"]
    with store.transaction() as c:
        assert states(c)[ops] == "sent"
        assert c.execute("SELECT status FROM maintenance_run_steps WHERE maintenance_run_id=?", (run["id"],)).fetchone()[0] == "pending"
        updates.post(c, ops, "- Shipped the pricing page", day=DAY)
    assert not scheduler.tick(TUESDAY)["failures"]
    done = maintenance.get_run(store, run["id"])
    assert done["steps"][0]["status"] == "ran" and done["steps"][0]["finished_at"], done
    assert done["steps"][0]["details"]["open"] == 0 and done["steps"][0]["found"] == 1
    assert not scheduler.tick(TUESDAY)["failures"]
    with store.read() as c:
        assert c.execute("SELECT cursor_at FROM maintenance_steps WHERE key='update'").fetchone()[0] == now
        assert c.execute("SELECT count(*) FROM update_queue").fetchone()[0] == queued, "the pump never rebuilds the cycle"
        assert c.execute("SELECT count(*) FROM events WHERE action=? AND target=?",
                         (maintenance_limits.BOT_ACTION, ops)).fetchone()[0] == 1


def test_the_end_of_run_completes_a_cycle_that_drained_after_the_step_returned(api, monkeypatch):
    store = api.app.state.store
    now = ctx(api, TUESDAY)["now"]
    monkeypatch.setattr(H, "now", lambda: now)
    monkeypatch.setattr(updates, "writer_for", lambda *args: None)
    with store.transaction() as c:
        turn(c, bid(c, "ops"))

    def drained(c, context):
        result = step.run(c, context)
        assert result["status"] == "pending"
        with store.transaction() as tx:
            tx.execute("UPDATE update_queue SET state='missed', done_at=? WHERE day=? AND state='queued'", (now, DAY))
        return result

    monkeypatch.setitem(maintenance.LIVE, "update", drained)
    run, _ = maintenance.run_now(store, api.app.state.auth, "human:ana", step="update", wait=True)
    assert run["steps"][0]["status"] == "ran" and run["steps"][0]["details"]["open"] == 0, run
    with store.read() as c:
        assert c.execute("SELECT cursor_at FROM maintenance_steps WHERE key='update'").fetchone()[0] == now


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
    result = run_step(api)
    with store.read() as c:
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
    run_step(api)
    with store.read() as c:
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
    run_step(api)
    with store.transaction() as c:
        reader = bid(c, "reader")
        row = c.execute("SELECT * FROM update_queue WHERE bot=? AND day=?", (reader, DAY)).fetchone()
        assert row["state"] == "queued" and row["delivery_only"] == 1, "no work, but learnings waiting: a delivery turn"
        c.execute("UPDATE update_queue SET state='skipped' WHERE day=? AND bot<>?", (DAY, reader))   # only this one goes out
        assert updates.dispatch(c, TUESDAY, build=False) == reader
        sent = c.execute("SELECT q.*, m.refs, m.body FROM update_queue q JOIN messages m ON m.id=q.message_id WHERE q.id=?",
                         (row["id"],)).fetchone()
        assert json.loads(sent["refs"])["delivery_only"] is True and "Keep the Friday cut-off" in sent["body"]
        assert "do not post one now" in sent["body"]
        # Its run completes and it posts nothing: the turn ends delivered_without_update, never retried.
        c.execute("INSERT OR REPLACE INTO jobs(id,message_id,bot_id,state,created_at) VALUES(?,?,?,'completed',?)",
                  (H.new_id(), sent["message_id"], reader, H.now()))
        assert updates.settle_sent(c, dict(sent)) is False
        assert c.execute("SELECT state FROM update_queue WHERE id=?", (row["id"],)).fetchone()["state"] == "delivered_without_update"
        assert updates.dispatch(c, TUESDAY, build=False) is None
        assert c.execute("SELECT count(*) FROM update_queue WHERE bot=? AND day=?", (reader, DAY)).fetchone()[0] == 1


def test_an_ask_that_completes_without_an_update_is_asked_again(api):
    store = api.app.state.store
    with store.transaction() as c:
        turn(c, bid(c, "ops"))
    run_step(api)
    with store.transaction() as c:
        ops = bid(c, "ops")
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
        turn(c, bid(c, "ops"))
    run_step(api)
    with store.transaction() as c:
        ops = bid(c, "ops")
        c.execute("UPDATE update_queue SET state='skipped' WHERE day=? AND bot<>?", (DAY, ops))
        assert updates.dispatch(c, TUESDAY, build=False) == ops
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
        turn(c, bid(c, "analyst"))
    run_step(api)
    with store.transaction() as c:
        analyst, boss = bid(c, "analyst"), bid(c, "boss")
        assert states(c)[boss] == "queued", "a manager is asked: its report will post today"
        c.execute("UPDATE update_queue SET state='skipped' WHERE day=? AND bot NOT IN (?,?)", (DAY, analyst, boss))
        assert updates.dispatch(c, TUESDAY, build=False) == analyst, "the report is asked first"
        assert updates.dispatch(c, TUESDAY, build=False) is None, "one request out at a time: the manager waits"
        updates.post(c, analyst, "- Sized the market", day=DAY)
        assert updates.dispatch(c, TUESDAY, build=False) == boss, "the manager is asked once its report has posted"


def test_a_manager_whose_reports_have_nothing_to_do_gets_no_request(api):
    bot(api, "boss2", "Manager", reports_to="human:ana")
    bot(api, "quiet-report", "Quiet", reports_to="boss2")
    store = api.app.state.store
    run_step(api)
    with store.read() as c:
        boss, report = bid(c, "boss2"), bid(c, "quiet-report")
        assert states(c)[report] == "skipped" and states(c)[boss] == "skipped", "neither has work: no roll-up is asked"


def test_a_bot_the_limiter_refuses_stays_queued_for_the_next_pass_and_each_request_is_recorded_once(api, monkeypatch):
    bot(api, "held", "Held")
    store = api.app.state.store
    with store.transaction() as c:
        turn(c, bid(c, "held"))
        turn(c, bid(c, "ops"))
    run_step(api)
    refused, recorded = set(), []
    monkeypatch.setattr(maintenance_limits, "allow", lambda c, bot_id, task_id=None: bot_id not in refused)
    monkeypatch.setattr(maintenance_limits, "record", lambda c, bot_id, task_ids=(): recorded.append(bot_id))
    with store.transaction() as c:
        held, ops = bid(c, "held"), bid(c, "ops")
        c.execute("UPDATE update_queue SET state='skipped' WHERE day=? AND bot NOT IN (?,?)", (DAY, held, ops))
        refused.add(held)
        assert updates.dispatch(c, TUESDAY, build=False) == ops, "the refused bot is passed over on this pass"
        assert states(c)[held] == "queued", "a refused request is not missed: it stays queued"
        assert recorded == [ops], "one update request is one wake"
        c.execute("UPDATE update_queue SET state='posted' WHERE bot=? AND day=?", (ops, DAY))
        refused.clear()
        assert updates.dispatch(c, TUESDAY, build=False) == held, "the next pass asks the held bot"
        assert recorded == [ops, held]


@pytest.mark.parametrize("limit", ["bot", "fleet"])
def test_update_honors_the_shared_wake_ledger_and_records_the_request(api, monkeypatch, limit):
    store = api.app.state.store
    now = ctx(api, TUESDAY)["now"]
    monkeypatch.setattr(H, "now", lambda: now)
    with store.transaction() as c:
        ops = bid(c, "ops")
        turn(c, ops)
    run_step(api)
    with store.transaction() as c:
        for bot_id in ([ops] if limit == "bot" else [f"fleet-{n}" for n in range(maintenance_limits.WAKES_PER_HOUR)]):
            maintenance_limits.record(c, bot_id)
        assert updates.dispatch(c, TUESDAY, build=False) is None
        assert states(c)[ops] == "queued", "a capped wake is left for the next pass"
    monkeypatch.setattr(H, "now", lambda: H.shift(now, minutes=61))
    with store.transaction() as c:
        assert updates.dispatch(c, TUESDAY, build=False) == ops
        assert c.execute("SELECT count(*) FROM events WHERE action=? AND target=? AND created_at>?",
                         (maintenance_limits.BOT_ACTION, ops, now)).fetchone()[0] == 1
        assert not maintenance_limits.allow(c, ops), "the request consumed its shared wake allowance"


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
        turn(c, bid(c, "ops"))
    result = run_step(api, FRIDAY)
    with store.read() as c:
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


def test_migration_0094_adds_the_columns_once_and_marks_old_updates_version_one():
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE updates(id TEXT PRIMARY KEY, bot TEXT, kind TEXT, day TEXT, headline TEXT, body TEXT, "
                 "created_at TEXT, updated_at TEXT)")
    conn.execute("CREATE TABLE update_queue(id TEXT PRIMARY KEY, bot TEXT, kind TEXT, day TEXT, rank INTEGER, state TEXT)")
    conn.execute("INSERT INTO updates VALUES('u','ops','daily','2026-09-28','h','- one','t','t')")
    migration = _migration("0094_update_format_and_delivery")
    migration.up(conn)
    migration.up(conn)
    cols = {r[1] for r in conn.execute("PRAGMA table_info(updates)")}
    assert {"format_version", "learnings_saved", "suggestions_seen", "delivery_batch_id"} <= cols
    assert conn.execute("SELECT format_version FROM updates").fetchone()[0] == 1
    assert {"delivery_only", "run_id"} <= {r[1] for r in conn.execute("PRAGMA table_info(update_queue)")}


def test_dispatch_with_build_off_never_starts_a_queue(api):
    store = api.app.state.store
    with store.transaction() as c:
        turn(c, bid(c, "ops"))
        assert updates.dispatch(c, TUESDAY, build=False) is None
        assert c.execute("SELECT count(*) FROM update_queue").fetchone()[0] == 0, "the step builds the queue, not the pump"
    run_step(api)
    with store.transaction() as c:
        assert updates.dispatch(c, TUESDAY, build=False) is not None
        assert c.execute("SELECT count(*) FROM update_queue").fetchone()[0] > 0


def test_the_step_is_pending_while_a_request_is_queued_or_out_and_ran_once_the_cycle_is_done(api):
    store = api.app.state.store
    with store.transaction() as c:
        ops = bid(c, "ops")
        turn(c, ops)
    assert run_step(api)["status"] == "pending"
    with store.transaction() as c:
        assert states(c)[ops] == "queued" and not updates.cycle_done(c, DAY)
        c.execute("UPDATE update_queue SET state='sent', sent_at=? WHERE day=? AND bot=?", (H.now(), DAY, ops))
        assert not updates.cycle_done(c, DAY), "a request that is out is not done"
    assert run_step(api)["status"] == "pending"
    with store.transaction() as c:
        c.execute("UPDATE update_queue SET state='posted', done_at=? WHERE day=?", (H.now(), DAY))
        assert updates.cycle_done(c, DAY)
    result = run_step(api)
    assert result["status"] == "ran" and result["details"]["open"] == 0
    with store.read() as c:
        assert {r["run_id"] for r in c.execute("SELECT run_id FROM update_queue WHERE day=?", (DAY,))} == {"run-test"}


def fake_answer(body, seen):
    """An opener that records each request and answers with `body`, the JSON a provider sends."""
    def opener(request, timeout=None):
        seen.append(request)
        return io.BytesIO(json.dumps(body).encode())
    return opener


def test_a_draft_that_is_not_json_is_still_metered_as_unusable_spend_and_asked_again(api):
    store = api.app.state.store
    with store.transaction() as c:
        c.execute("INSERT INTO update_queue(id,bot,kind,day,rank,state,sent_at) "
                  "VALUES('draft-bad','human:ana','daily',?,0,'sent',?)", (DAY, H.now()))
    answer = {"choices": [{"message": {"content": "Here is your update, not JSON"}}],
              "usage": {"prompt_tokens": 120, "completion_tokens": 40}}
    write = llm_writer("openai", "key", "acme-draft-model", opener=fake_answer(answer, []))
    updates.write_draft(store, "human:ana", write, "coo")
    with store.read() as c:
        spend = c.execute("SELECT target, detail FROM events WHERE action='judge.spend'").fetchall()
        row = c.execute("SELECT state, tries FROM update_queue WHERE id='draft-bad'").fetchone()
    assert [r["target"] for r in spend] == ["update-draft"]
    detail = json.loads(spend[0]["detail"])
    assert detail["model"] == "acme-draft-model" and detail.get("unusable") is True
    assert (row["state"], row["tries"]) == ("queued", 1), "the draft is asked again, once"


WRITER_ANSWERS = {
    "openai": {"choices": [{"message": {"content": '{"body": "- Shipped"}'}}], "usage": {"prompt_tokens": 10, "completion_tokens": 5}},
    "anthropic": {"content": [{"type": "text", "text": '{"body": "- Shipped"}'}], "usage": {"input_tokens": 10, "output_tokens": 5}},
    "google": {"candidates": [{"content": {"parts": [{"text": '{"body": "- Shipped"}'}]}}], "usageMetadata": {"promptTokenCount": 10}},
}
OUTPUT_CAP = {"openai": lambda b: b["max_completion_tokens"], "anthropic": lambda b: b["max_tokens"],
              "google": lambda b: b["generationConfig"]["maxOutputTokens"]}


@pytest.mark.parametrize("provider", sorted(WRITER_ANSWERS))
def test_every_provider_writer_caps_the_answer_at_the_output_limit(provider):
    seen = []
    write = llm_writer(provider, "key", "acme-model", opener=fake_answer(WRITER_ANSWERS[provider], seen))
    assert write("system", "user") == {"body": "- Shipped"}
    assert OUTPUT_CAP[provider](json.loads(seen[0].data)) == 4096


def test_the_gateway_writer_caps_the_answer_at_the_output_limit():
    seen = []
    write = gateway_writer(lambda: "gw-key", "https://gateway.example/v1", "acme-model",
                           opener=fake_answer(WRITER_ANSWERS["openai"], seen))
    assert write("system", "user") == {"body": "- Shipped"}
    assert json.loads(seen[0].data)["max_completion_tokens"] == 4096


def test_a_friday_request_carries_the_quiet_tasks_learnings_and_suggestions_too(api, monkeypatch):
    slots = {"quiet_tasks": ["Quiet: Acme refund"], "learnings": ["Keep the refund rule"],
             "suggestions": ["Trim the weekly report"]}
    monkeypatch.setattr(updates, "request_slots", lambda c, bot: slots)
    with api.app.state.store.read() as c:
        ops = bid(c, "ops")
        weekly = updates.request_text(c, ops, "weekly", "2026-10-02")
        daily = updates.request_text(c, ops, "daily", "2026-10-02")
    for text in (weekly, daily):
        assert "Quiet: Acme refund" in text and "Keep the refund rule" in text and "Trim the weekly report" in text
