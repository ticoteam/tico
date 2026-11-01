"""The scheduler tick never holds the write lock for the whole pass (support ticket TK-RCZJXEWA).

A tick on a hub with many tasks and routines (schedule automations now) used to run every step, every routine and the
due-date reminder scan in one write transaction; the reminder scan read every task row under it, so the lock was held 20-30 s.
Work under the lock is counted in SQLite VM instructions, not seconds, so the bound holds on a slow machine.
"""
import json
import time
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone

from backend.tests.test_api import api, post  # noqa: F401
from backend import routines
from backend.scheduler import Scheduler, stamp
from backend.store import H

AT = datetime(2026, 9, 10, 17, 0, tzinfo=timezone.utc)
TASKS = 40000
ROUTINES = 80
# Instructions a single write transaction may run, in units of STEP. A full scan of the seeded tasks table
# runs about a million; a step, a routine or a reminder batch here runs well under a quarter of that.
STEP = 1000
BOUND = 250


def seed(store):
    """Many wide finished and live tasks, a few hundred with due dates that keep coming back, and many
    routines not yet due."""
    body = "x" * 2000
    rows = []
    for i in range(TASKS):
        status = ("done", "closed", "open", "doing", "review")[i % 5]
        owner = "bot:coo" if i % 3 else "ana"
        due = None
        if i % 50 == 0:
            # due tomorrow (inside the day of slack), or long past and owned by a paused bot
            due = stamp(AT + timedelta(hours=10)) if i % 100 else "2026-09-01"
            owner = "bot:coo" if i % 100 else "bot:finance"
        rows.append((str(uuid.uuid4()), f"Task {i}", body, "ana", owner, status, due, None,
                     "2026-01-01T00:00:00Z", "2026-01-01T00:00:00Z"))
    with store.transaction() as c:
        c.executemany("INSERT INTO tasks(id,title,body,requester,owner,status,due,conversation_id,created_at,updated_at) "
                      "VALUES(?,?,?,?,?,?,?,?,?,?)", rows)
        for i in range(ROUTINES):                      # each moves to its schedule automation
            routine(c, f"r{i}", f"Routine {i}")
        c.execute("UPDATE bots SET state='paused' WHERE slug='finance'")


def routine(c, key, title, due=None):
    """A routine of the COO's, seeded the way Tico seeds one: its schedule automation, due at `due` when given."""
    aid = routines.create(c, H.KEEPER, "coo", {"title": title, "cron": "0 9 * * *", "timezone": "UTC",
                                                "text": "Do it"}, key=key)["automation"]
    if due:
        c.execute("UPDATE automations SET next_due=? WHERE id=?", (due, aid))
    return aid


def watch(store, monkeypatch):
    """Every write transaction the tick opens: its instruction count and how long it held the lock."""
    seen = []
    original = store.transaction

    @contextmanager
    def counted():
        with original() as c:
            entry = {"steps": 0, "started": time.monotonic()}

            def tick():
                entry["steps"] += 1
                return 0
            c.set_progress_handler(tick, STEP)
            try:
                yield c
            finally:
                c.set_progress_handler(None, 0)
                entry["ms"] = (time.monotonic() - entry["started"]) * 1000
                seen.append(entry)
    monkeypatch.setattr(store, "transaction", counted)
    return seen


def test_tick_holds_the_write_lock_only_briefly_per_step(api, monkeypatch):
    store = api.app.state.store
    seed(store)
    scheduler = Scheduler(store, api.app.state.execution)
    scheduler.tick(AT)                                  # first tick: every routine gets its next_due
    seen = watch(store, monkeypatch)
    started = time.monotonic()
    result = scheduler.tick(AT + timedelta(seconds=10))
    tick_ms = (time.monotonic() - started) * 1000
    assert not result["failures"]
    assert seen, "the tick writes its health row"
    worst = max(seen, key=lambda e: e["steps"])
    print(f"tick {tick_ms:.0f} ms, {len(seen)} write transactions, longest hold {max(e['ms'] for e in seen):.0f} ms, "
          f"most work {worst['steps'] * STEP} instructions")
    assert worst["steps"] < BOUND, worst
    with store.read() as c:
        health = json.loads(c.execute("SELECT detail FROM service_health WHERE service='scheduler'").fetchone()[0])
    assert {"tick_ms", "hold_ms", "hold_step", "wait_ms"} <= set(health)


def test_hold_ms_is_the_hold_not_the_wait_behind_another_writer(api):
    """Another writer holds the lock for a second as the tick starts: that shows as wait_ms, not hold_ms."""
    import threading
    store = api.app.state.store
    taken = threading.Event()

    def hold():
        with store.read() as other:
            other.execute("BEGIN IMMEDIATE")
            taken.set()
            time.sleep(1)
            other.execute("ROLLBACK")
    holder = threading.Thread(target=hold)
    holder.start()
    taken.wait(5)
    Scheduler(store, api.app.state.execution).tick(AT)
    holder.join()
    with store.read() as c:
        health = json.loads(c.execute("SELECT detail FROM service_health WHERE service='scheduler'").fetchone()[0])
    assert health["wait_ms"] >= 900 and health["hold_ms"] < 900, health


def plan(c, sql, *args):
    return " | ".join(row[-1] for row in c.execute("EXPLAIN QUERY PLAN " + sql, args))


def test_tick_task_queries_read_an_index_not_the_tasks_table(api):
    with api.app.state.store.read() as c:
        due = plan(c, H.TASKS_DUE_SQL, "2026-09-11")
        assert "tasks_due_active" in due and "SCAN tasks" not in due.replace("SCAN tasks USING", ""), due
        from backend import task_rules
        rules = plan(c, task_rules.DUE_SQL, *("2026-09-11T00:00:00Z",) * 5, 50)
        assert "tasks_rule_done_due" in rules and "TEMP B-TREE" not in rules, rules
        assert "SCAN t" not in rules.replace("SCAN t USING", ""), rules
        assert not c.execute("SELECT 1 FROM sqlite_master WHERE name='tasks_rule_due'").fetchone()
        from backend import quiet_work
        resume = plan(c, quiet_work.DUE_SQL, "2026-09-11T00:00:00Z")
        assert "tasks_resume_due" in resume and "SCAN tasks" not in resume.replace("SCAN tasks USING", ""), resume
        leases = plan(c, "SELECT id FROM attempts WHERE state IN ('leased','running') AND lease_until<=? ORDER BY lease_until,id LIMIT 8", H.now())
        assert 'attempts_live_lease' in leases and 'TEMP B-TREE' not in leases, leases


def test_split_tick_still_fires_each_occurrence_and_reminder_once(api):
    store = api.app.state.store
    task = post(api, "tasks", {"owner": "coo", "title": "File the report", "body": "Due today",
                               "due": "2026-09-10T15:00:00Z"})
    with store.transaction() as c:
        made = [routine(c, f"m{i}", f"Morning {i}", due="2026-09-09T16:00:00.000000Z") for i in range(3)]
    # Two schedulers, as across a restart: neither the occurrences nor the reminder fire twice.
    first = Scheduler(store, api.app.state.execution).tick(AT)
    second = Scheduler(store, api.app.state.execution).tick(AT)
    assert not first["failures"] and not second["failures"]
    with store.read() as c:
        runs = [r[0] for r in c.execute("SELECT automation_id FROM automation_runs WHERE automation_id IN (?,?,?)", made)]
        assert sorted(runs) == sorted(made)
        assert c.execute("SELECT count(*) FROM tasks WHERE owner='bot:coo' AND title LIKE 'Morning %'").fetchone()[0] == 3
        assert c.execute("SELECT count(*) FROM task_reminders WHERE task_id=?", (task["id"],)).fetchone()[0] == 1
        assert c.execute("SELECT count(*) FROM messages WHERE conversation_id=? AND body LIKE 'Due: %'",
                         (task["conversation_id"],)).fetchone()[0] == 1


def test_reminder_rechecks_the_task_under_the_lock(api, monkeypatch):
    """A task done or moved between the read and the write gets no reminder."""
    store = api.app.state.store
    task = post(api, "tasks", {"owner": "coo", "title": "Moved", "body": "b", "due": "2026-09-10T15:00:00Z"})
    original = H.tasks_due_for_bots

    def read_then_finish(c, through):
        rows = original(c, through)
        with store.transaction() as w:
            w.execute("UPDATE tasks SET status='done' WHERE id=?", (task["id"],))
        return rows
    monkeypatch.setattr(H, "tasks_due_for_bots", read_then_finish)
    Scheduler(store, api.app.state.execution).tick(AT)
    with store.read() as c:
        assert not c.execute("SELECT 1 FROM task_reminders WHERE task_id=?", (task["id"],)).fetchone()


def test_reminder_sent_meanwhile_is_not_sent_again(api, monkeypatch):
    """Another server's tick sends the reminder between this tick's read and its write: one message, no failure."""
    store = api.app.state.store
    task = post(api, "tasks", {"owner": "coo", "title": "Raced", "body": "b", "due": "2026-09-10T15:00:00Z"})
    import backend.scheduler as scheduler
    original = scheduler.remind

    def read_then_race(c, row, at):
        due = original(c, row, at)
        monkeypatch.setattr(scheduler, "remind", original)
        Scheduler(store, api.app.state.execution).tick(AT)
        return due
    monkeypatch.setattr(scheduler, "remind", read_then_race)
    result = Scheduler(store, api.app.state.execution).tick(AT)
    assert not result["failures"]
    with store.read() as c:
        assert c.execute("SELECT count(*) FROM messages WHERE conversation_id=? AND body LIKE 'Due: %'",
                         (task["conversation_id"],)).fetchone()[0] == 1
