"""Find stuck (backend/maintenance_steps/find_stuck.py): the Maintenance step that gives each bot its task roundup list. It
reads the bots on the connection it is handed, sends each one through the store under the wake limiter, and records a
delivered list once, with the tasks it named; a bot the limiter refuses is left unsent and nothing is recorded for it.
Migration 0093 withdraws the open Still wanted questions to people, and only those. Synthetic fixtures only."""

import importlib
import sqlite3

from backend import task_check_backs, task_roundup
from backend.store import H
from backend.maintenance_steps import find_stuck as FS
from backend.tests.test_api import api  # noqa: F401  (fixture)
from backend.tests.test_task_roundup import (  # noqa: F401
    DAY, NOON, ago, blocks, build_clock, complete_jobs, lists, named_in, only_list, settle, started, task,
)


def run_find_stuck(store, at=DAY, settings=None):
    ctx = {"store": store, "now": at}
    if settings is not None:
        ctx["settings"] = settings
    with store.read_transaction() as c:
        return FS.run(c, ctx)


def test_the_step_reads_the_bots_on_its_connection_and_sends_each_through_the_store(api):
    store = api.app.state.store
    with store.transaction() as c:
        tid = task(c, "Draft the grant report")["id"]
        settle(c, tid, "finance", ago(days=3))
        complete_jobs(c)
    result = run_find_stuck(store)
    assert result["status"] == "ran"
    assert result["details"]["woken"] == ["finance"]
    assert result["woke"] == 1 and result["found"] == 1 and result["checked"] >= 1
    with store.read() as c:
        assert f"{tid[:8]} Draft the grant report: Quiet for 3 days, nothing waiting on anyone" in only_list(c)


def test_a_bot_the_limiter_refuses_is_left_unsent_and_nothing_is_recorded_for_it(api, monkeypatch):
    store = api.app.state.store
    monkeypatch.setattr(FS.LIMITS, "allow", lambda c, bot_id, task_id=None: False)
    with store.transaction() as c:
        tid = task(c, "Draft the grant report")["id"]
        settle(c, tid, "finance", ago(days=3))
        complete_jobs(c)
    result = run_find_stuck(store)
    assert result["woke"] == 0 and result["details"]["refused"] == ["finance"]
    with store.read() as c:
        assert lists(c) == [], "no list was written for a refused bot"
        assert task_roundup._mark(c, task_roundup.SENT_KEY + "finance") is None
        assert task_roundup._mark(c, task_roundup.LISTED_KEY + tid) is None


def test_a_delivered_list_is_recorded_once_with_every_task_it_named(api, monkeypatch):
    store = api.app.state.store
    calls = []
    monkeypatch.setattr(FS.LIMITS, "record", lambda c, bot_id, task_ids=(): calls.append((bot_id, list(task_ids))))
    with store.transaction() as c:
        first = task(c, "Draft the grant report")["id"]
        second = task(c, "Chase the quote")["id"]
        settle(c, first, "finance", ago(days=3))
        settle(c, second, "finance", ago(days=3))
        complete_jobs(c)
    assert run_find_stuck(store)["woke"] == 1
    assert len(calls) == 1 and calls[0][0] == "finance"
    assert sorted(calls[0][1]) == sorted([first, second]), "one record per delivered list, with every task it named"


def test_the_step_sends_at_most_max_bots_oldest_line_first_and_the_rest_wait_for_the_next_run(api):
    store = api.app.state.store
    with store.transaction() as c:
        # Created finance, coo, ops: neither creation order nor name order is the age order (ops, coo, finance).
        for slug, title, turned in (("finance", "Draft the grant report", ago(days=9)), ("coo", "Sign the menu", ago(days=10)),
                                    ("ops", "Print the menu", ago(days=12))):
            settle(c, task(c, title, owner=H.bot_actor(slug))["id"], slug, turned)
            complete_jobs(c, slug)
    assert run_find_stuck(store, settings={"max_bots": 2})["details"]["woken"] == ["ops", "coo"]
    with store.read_transaction() as c:
        assert lists(c, "finance") == [], "the bot past the cap is not sent this run"
    assert run_find_stuck(store, at=NOON, settings={"max_bots": 2})["details"]["woken"] == ["finance"]


def test_a_task_the_limiter_holds_is_dropped_from_its_list_while_an_eligible_one_is_sent(api, monkeypatch):
    store = api.app.state.store
    with store.transaction() as c:
        held = task(c, "Draft the grant report")["id"]
        eligible = task(c, "Chase the quote")["id"]
        settle(c, held, "finance", ago(days=3))
        settle(c, eligible, "finance", ago(days=3))
        complete_jobs(c)
    monkeypatch.setattr(FS.LIMITS, "allow", lambda c, bot_id, task_id=None: task_id != held)
    result = run_find_stuck(store)
    assert result["details"]["woken"] == ["finance"]
    assert result["details"]["limited"] == [held]
    with store.read() as c:
        body = only_list(c)
        assert named_in(body) == 1 and f"{eligible[:8]} Chase the quote" in body
        assert held[:8] not in body
        assert task_roundup._mark(c, task_roundup.LISTED_KEY + held) is None, "a held task is not recorded as listed"


def test_replacements_past_the_display_cap_also_pass_the_task_limit_in_the_send_transaction(api, monkeypatch):
    store = api.app.state.store
    tids = []
    with store.transaction() as c:
        for n in range(28):
            tid = task(c, f"Review invoice {n}")["id"]
            settle(c, tid, "finance", ago(days=3, minutes=28 - n))
            tids.append(tid)
        complete_jobs(c)
    daily_wakes = {tids[0]: 3, tids[25]: 3}     # one displayed task and the first replacement used their daily allowance
    checked, recorded = [], []

    def allow(c, bot_id, task_id=None):
        assert c.write_transaction and c.in_transaction
        checked.append((c, bot_id, task_id))
        return daily_wakes.get(task_id, 0) < 3

    def record(c, bot_id, task_ids=()):
        assert c.write_transaction and c.in_transaction
        assert all((c, bot_id, tid) in checked for tid in task_ids), "each delivered task passed in this transaction"
        recorded.append((bot_id, list(task_ids)))

    monkeypatch.setattr(FS.LIMITS, "allow", allow)
    monkeypatch.setattr(FS.LIMITS, "record", record)
    result = run_find_stuck(store)
    delivered = [tid for tid in tids if tid not in daily_wakes][:25]
    assert result["details"]["woken"] == ["finance"]
    assert result["details"]["limited"] == sorted(daily_wakes)
    assert recorded == [("finance", delivered)]
    with store.read() as c:
        body = only_list(c)
        assert named_in(body) == 25
        assert all(tid[:8] in body for tid in delivered)
        for tid in (*daily_wakes, tids[-1]):
            assert tid[:8] not in body
            assert task_roundup._mark(c, task_roundup.LISTED_KEY + tid) is None


def test_a_blocker_past_the_bot_cap_gets_its_line_on_the_next_run(api):
    store = api.app.state.store
    with store.transaction() as c:
        mine = task(c, "Print the menu")["id"]
        settle(c, mine, "finance", ago(days=1))
        c.execute("UPDATE tasks SET status='waiting' WHERE id=?", (mine,))
        for slug in ("coo", "ops"):
            blocker = task(c, f"Finish the menu for {slug}", owner=H.bot_actor(slug))["id"]
            settle(c, blocker, slug, ago(hours=2))
            blocks(c, blocker, mine, ago(days=1))
            complete_jobs(c, slug)
        complete_jobs(c)
    assert run_find_stuck(store, settings={"max_bots": 1})["details"]["woken"] == ["coo"]
    with store.read() as c:
        assert f"{mine[:8]} Print the menu: Waiting on you" in only_list(c, "coo")
        assert lists(c, "ops") == [], "the second blocker waits for the next run"
    assert run_find_stuck(store, at=NOON, settings={"max_bots": 1})["details"]["woken"] == ["ops"]
    with store.read() as c:
        assert f"{mine[:8]} Print the menu: Waiting on you" in only_list(c, "ops")
        assert len(lists(c, "coo")) == 1, "the delivered recipient is not listed again"


def test_the_oldest_eligible_line_orders_bots_even_when_it_is_past_the_display_cap(api, monkeypatch):
    store = api.app.state.store
    monkeypatch.setattr(task_roundup, "MAX_LINES", 1)
    with store.transaction() as c:
        due = task(c, "Chase the quote")["id"]
        settle(c, due, "finance", ago(days=3))
        task_check_backs.set_time(c, H.task(c, due), ago(hours=1), "Check the quote",
                                  actor=H.human_actor("ana"), at=ago(hours=3))
        oldest = task(c, "Renew the lease")["id"]
        settle(c, oldest, "finance", ago(days=12))
        other = task(c, "Sign the menu", owner=H.bot_actor("coo"))["id"]
        settle(c, other, "coo", ago(days=5))
        complete_jobs(c)
        complete_jobs(c, "coo")
    assert run_find_stuck(store, settings={"max_bots": 1})["details"]["woken"] == ["finance"]
    with store.read() as c:
        body = only_list(c)
        assert due[:8] in body and oldest[:8] not in body, "the oldest line set priority before the display cap"
        assert lists(c, "coo") == []


def test_migration_0093_withdraws_only_the_keepers_open_still_wanted_questions():
    mig = importlib.import_module("backend.migrations.0093_withdraw_still_wanted")
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE messages(id TEXT, sender_id TEXT, answered_by_id TEXT, refs TEXT, superseded_at TEXT)")
    conn.executemany("INSERT INTO messages(id,sender_id,answered_by_id,refs,superseded_at) VALUES(?,?,?,?,NULL)", [
        ("open", "keeper", None, '{"still_wanted": true}'),
        ("answered", "keeper", "human:ana", '{"still_wanted": true}'),
        ("person", "human:ana", None, '{"still_wanted": true}'),
        ("ask", "keeper", None, '{"ask": true}'),
    ])
    mig.up(conn)
    flags = dict(conn.execute("SELECT id, superseded_at IS NOT NULL FROM messages").fetchall())
    assert flags == {"open": 1, "answered": 0, "person": 0, "ask": 0}
    stamp = conn.execute("SELECT superseded_at FROM messages WHERE id='open'").fetchone()[0]
    mig.up(conn)
    assert conn.execute("SELECT superseded_at FROM messages WHERE id='open'").fetchone()[0] == stamp, "running again changes nothing"


def test_migration_0093_skips_a_database_with_no_superseded_column():
    mig = importlib.import_module("backend.migrations.0093_withdraw_still_wanted")
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE messages(id TEXT, sender_id TEXT, refs TEXT)")
    mig.up(conn)
