"""Migration 20261126063935_attempts_idle_s, on a database an older server left: the column is added, resumed runs from
the last 35 days get how long their session sat idle since the bot's last run on the same task, older runs and new
sessions keep NULL, and running it again changes nothing."""

import importlib
from datetime import datetime, timedelta, timezone

MIGRATION = importlib.import_module("backend.migrations.20261126063935_attempts_idle_s")


def at(days=0, minutes=0):
    moment = datetime.now(timezone.utc) - timedelta(days=days, minutes=minutes)
    return moment.strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def idle(c, rows):
    marks = ",".join("?" * len(rows))
    return dict(c.execute(f"SELECT id, idle_s FROM attempts WHERE id IN ({marks})", [r[0] for r in rows]).fetchall())


def test_resumed_runs_from_the_last_35_days_get_their_idle_time_and_running_it_again_changes_nothing(api):
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
        ]
        for rid, created, settled, kind, tid, *started in rows:
            c.execute(
                "INSERT INTO attempts(id, bot_id, created_at, started_at, settled_at, exit, task_id, session_kind) "
                "VALUES(?, 'ops', ?, ?, ?, 'completed', ?, ?)",
                (rid, created, started[0] if started else None, settled, tid, kind),
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
        }
        MIGRATION.up(c)
        assert idle(c, rows) == got
