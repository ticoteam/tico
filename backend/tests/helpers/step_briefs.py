"""Shared by test_step_briefs.py and bench/perf-status-line.py: a synthetic run's events in a bare SQLite file, and the
status line as it was made before step briefs (backend/step_briefs.py)."""

import json
import sqlite3

from backend import step_briefs, thread_bot_status
from backend.turns import reply_parts

# The briefs table and its triggers as backend/migrations/baseline.sql has them (migration attempt_event_briefs, retired
# with the v0.6.0 floor).
BRIEFS = (
    "CREATE TABLE IF NOT EXISTS attempt_event_briefs("
    "attempt_id TEXT NOT NULL, seq INTEGER NOT NULL, brief TEXT NOT NULL, "
    "PRIMARY KEY(attempt_id,seq)) WITHOUT ROWID",
    "CREATE TRIGGER IF NOT EXISTS attempt_event_briefs_rewritten AFTER UPDATE OF payload ON attempt_events BEGIN "
    "DELETE FROM attempt_event_briefs WHERE attempt_id=OLD.attempt_id AND seq=OLD.seq; END",
    "CREATE TRIGGER IF NOT EXISTS attempt_event_briefs_deleted AFTER DELETE ON attempt_events BEGIN "
    "DELETE FROM attempt_event_briefs WHERE attempt_id=OLD.attempt_id AND seq=OLD.seq; END",
)


def briefs_schema(c):
    """The briefs table and its triggers, over the caller's bare `attempt_events`."""
    for sql in BRIEFS:
        c.execute(sql)


def replayed(c, attempt_id):
    """The status line as it was made before briefs: decode the newest STEP_EVENTS events whole and replay them."""
    rows = []
    for kind, payload, created in c.execute(
        "SELECT kind, payload, created_at FROM attempt_events WHERE attempt_id=? AND kind IN ('delta','message','tool') "
        "ORDER BY seq DESC LIMIT ?",
        (attempt_id, thread_bot_status.STEP_EVENTS),
    ):
        try:
            value = json.loads(payload)
        except (TypeError, ValueError):
            value = {}
        rows.append((kind, value if isinstance(value, dict) else {}, created))
    newest = rows[0] if rows else None
    if newest and newest[0] == "delta" and newest[1].get("delta_kind") == "thought":
        return thread_bot_status.THINKING
    parts = reply_parts(list(reversed(rows)))
    if not parts:
        return thread_bot_status.STARTING
    last = parts[-1]
    if last["kind"] == "reply":
        return "Writing a reply"
    text = " ".join(str(last["text"]).split())
    limit = thread_bot_status.STEP_CHARS
    return text if len(text) <= limit else text[: limit - 1] + "…"


def database():
    c = sqlite3.connect(":memory:")
    c.execute(
        "CREATE TABLE attempt_events(id INTEGER PRIMARY KEY AUTOINCREMENT, attempt_id TEXT, seq INTEGER, kind TEXT, "
        "payload TEXT, created_at TEXT, UNIQUE(attempt_id,seq))"
    )
    briefs_schema(c)
    return c


def add(c, seq, kind, payload, brief=True):
    stored = json.dumps(payload)
    c.execute(
        "INSERT INTO attempt_events(attempt_id,seq,kind,payload,created_at) VALUES('a',?,?,?,'2026-11-12T00:00:00Z')",
        (seq, kind, stored),
    )
    if brief:
        step_briefs.record(c, "a", [(seq, kind, stored, payload)])
