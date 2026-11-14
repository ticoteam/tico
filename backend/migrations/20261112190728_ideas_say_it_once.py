"""Ideas: say it once (Product task #209). Product's Ideas stops being a stage that tasks leave by hand: an idea that is
not snoozed moves on to Spec by itself when Spec has room (backend/idea_checks.py).

- `snoozes.filed`: 1 on a snooze written as a bot filed its own idea into Ideas. Only a person ends one.
- `idea_checks`: one row per idea filed into Ideas, owed until Jev's duplicate check answered (or gave up).
- Product's Ideas: `by_hand` and `requests_move_on` off, where it still has the preset's shape (both on).
- At release, once: each open idea in Ideas that a bot asked for, and nobody snoozed, is snoozed the same way; and every
  open idea there is owed one duplicate check, so one Jev pass runs over Ideas.

Running it again changes nothing.
"""

import uuid
from datetime import datetime, timezone

IDEAS = "product-ideas"
WHY = "A bot's idea: unsnooze to send it on."


def _table(conn, name):
    return conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)).fetchone() is not None


def _columns(conn, name):
    return {row[1] for row in conn.execute(f"PRAGMA table_info({name})")}


def up(conn):
    if _table(conn, "snoozes") and "filed" not in _columns(conn, "snoozes"):
        conn.execute("ALTER TABLE snoozes ADD COLUMN filed INTEGER NOT NULL DEFAULT 0")
    conn.execute(
        "CREATE TABLE IF NOT EXISTS idea_checks ("
        "task_id TEXT PRIMARY KEY, visit_id TEXT, owed_at TEXT NOT NULL, checked_at TEXT, outcome TEXT, "
        "same_id TEXT, confidence REAL, call_id TEXT)"
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idea_checks_owed ON idea_checks(owed_at) WHERE checked_at IS NULL")
    if not all(_table(conn, name) for name in ("stages", "tasks", "visits", "snoozes")):
        return
    if "requests_move_on" in _columns(conn, "stages"):
        conn.execute(
            "UPDATE stages SET by_hand=0, requests_move_on=0 WHERE id=? AND by_hand=1 AND requests_move_on=1", (IDEAS,)
        )
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
    ideas = conn.execute(
        "SELECT t.id, v.id, t.requester_id, EXISTS (SELECT 1 FROM snoozes z WHERE z.task_id = t.id AND z.ended_at IS NULL) "
        "FROM tasks t JOIN visits v ON v.task_id = t.id AND v.left_at IS NULL "
        "WHERE t.stage_id=? AND t.status='open' ORDER BY v.entered_at, v.id",
        (IDEAS,),
    ).fetchall()
    for task_id, visit_id, requester, snoozed in ideas:
        if str(requester or "").startswith("bot:") and not snoozed:
            conn.execute(
                "INSERT INTO snoozes (id, task_id, visit_id, by_id, why, until_at, created_at, filed) "
                "VALUES (?,?,?,?,?,NULL,?,1)",
                (str(uuid.uuid4()), task_id, visit_id, requester, WHY, now),
            )
        conn.execute(
            "INSERT OR IGNORE INTO idea_checks (task_id, visit_id, owed_at) VALUES (?,?,?)", (task_id, visit_id, now)
        )
