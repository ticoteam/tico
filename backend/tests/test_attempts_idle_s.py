"""Migration 20261126063935_attempts_idle_s, on a database an older server left: the column is added, resumed runs from
the last 35 days get how long their session sat idle since the bot's last run on the same task, older runs and new
sessions keep NULL, and running it again changes nothing."""

import importlib
from datetime import datetime, timedelta, timezone

from backend.tests.helpers.api import claim, post
from backend.tests.helpers.run_triggers import machine

MIGRATION = importlib.import_module("backend.migrations.20261126063935_attempts_idle_s")


def at(days=0, minutes=0):
    moment = datetime.now(timezone.utc) - timedelta(days=days, minutes=minutes)
    return moment.strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def idle(c, rows):
    ids = [r[0] for r in rows] + ["k4-lease", "k5-lease"]
    marks = ",".join("?" * len(ids))
    return dict(c.execute(f"SELECT id, idle_s FROM attempts WHERE id IN ({marks})", ids).fetchall())


def test_resumed_runs_from_the_last_35_days_get_their_idle_time_and_running_it_again_changes_nothing(api):
    r = machine(api)
    post(api, "tasks", {"owner": "ops", "title": "Watch the release", "body": "Each hour."})
    lease = claim(api, r)  # a real job and computer for the leases below to point at
    with api.app.state.store.transaction() as c:
        c.execute("ALTER TABLE attempts DROP COLUMN idle_s")
        rows = [
            # id, claimed (created_at), settled, session, task[, started]: two days ago the bot ran, then resumed 61 minutes after it settled
            ("first", at(2, 120), at(2, 100), "new", "k1"),
            ("cold", at(2, 39), at(2, 30), "resumed", "k1"),
            ("warm", at(2, 1), at(2, 0), "resumed", "k1"),  # 29 minutes after `cold` settled
            ("fresh", at(1, 0), at(1, -5), "new", "k1"),  # a new session: never given an idle time
            ("late-first", at(3, 100), at(3, 90), "new", "k3"),
            # Claimed 59 minutes after `late-first` settled, started at 61: measured to its start.
            ("late", at(3, 31), at(3, 20), "resumed", "k3", at(3, 29)),
            ("old-first", at(40, 100), at(40, 90), "new", "k2"),
            ("old", at(40, 0), at(40, -5), "resumed", "k2"),  # before the 35 days
            ("chat", at(1, 0), at(1, -5), "resumed", None),  # no task: nothing to measure from
            # A real run with no lease, then a lease handed back unstarted 2 minutes before a retry 68 minutes later:
            # the lease never ran, so the retry is measured from the real run and is cold.
            ("k4-first", at(4, 100), at(4, 90), "new", "k4"),
            ("k4-retry", at(4, 22), at(4, 20), "resumed", "k4"),
            # A leased run that reported events but never a start (an older runner) still ran: measured from it.
            ("k5-first", at(5, 100), at(5, 90), "new", "k5"),
            ("k5-retry", at(5, 61), at(5, 60), "resumed", "k5"),
        ]
        for rid, created, settled, kind, tid, *started in rows:
            c.execute(
                "INSERT INTO attempts(id, bot_id, created_at, started_at, settled_at, exit, task_id, session_kind) "
                "VALUES(?, 'ops', ?, ?, ?, 'completed', ?, ?)",
                (rid, created, started[0] if started else None, settled, tid, kind),
            )
        job, runner = c.execute("SELECT job_id, runner_id FROM attempts WHERE id=?", (lease["id"],)).fetchone()
        for rid, created, settled, tid, seq in (
            ("k4-lease", at(4, 25), at(4, 24), "k4", 0),
            ("k5-lease", at(5, 70), at(5, 69), "k5", 7),
        ):
            c.execute(
                "INSERT INTO attempts(id, job_id, bot_id, runner_id, generation, token_hash, state, lease_until, "
                "created_at, settled_at, exit, task_id, last_seq) VALUES(?,?,'ops',?,1,?,'expired',?,?,?,'expired',?,?)",
                (rid, job, runner, rid, settled, created, settled, tid, seq),
            )
        MIGRATION.up(c)
        got = idle(c, rows)
        assert got == {
            "first": None,
            "cold": 61 * 60,
            "warm": 29 * 60,
            "fresh": None,
            "late-first": None,
            "late": 61 * 60,
            "old-first": None,
            "old": None,
            "chat": None,
            "k4-first": None,
            "k4-retry": 68 * 60,
            "k4-lease": None,
            "k5-first": None,
            "k5-retry": 8 * 60,
            "k5-lease": None,
        }
        MIGRATION.up(c)
        assert idle(c, rows) == got
