"""The Nightly memory tidy pilot is retired: Dream (a Maintenance step) does a bot's nightly memory work. Nothing can file
a pass or a card any more, and nothing sweeps them, so what the pilot left open ends here:
  - an open tidy card leaves Needs you: withdrawn, reason `retired`, as its hourly sweep withdrew an expired one;
  - a Keep still queued or applying is refused with a plain reason (a runner is never handed it again);
  - a pass still filed is skipped, reason `skipped: retired`, and its two private tasks (the container and its check
    subtask) still on the board are closed by the keeper, each with a history event, as the pass's own sweep closed them;
  - the pilot list (`memory_tidy:pilots`) and the job's switch in the system_routines record are cleared.

The tables stay as history: memory_tidy_passes and memory_tidy_packets (0038) on every file, memory_tidy_cards and
memory_tidy_keeps where the pilot's code made them (a new file no longer has those two). Dream's auto check still reads
memory_tidy_passes to leave the pilot's runs out of a bot's memory count (backend/dream_auto.py).

Plain SQL: a migration never calls app code that may change later. Running it again changes nothing.
"""
import json
import uuid
from datetime import datetime, timezone

KEEPER = "keeper"
ACTIVE = ("open", "doing", "waiting", "review", "ready")     # H.ACTIVE_STATUSES: still on the board
NOTE = "Nightly memory tidy retired"
KEEP_REFUSED = "Nightly memory tidy is retired; nothing was changed"


def _now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f") + "Z"


def up(conn):
    tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    when = _now()
    if "memory_tidy_keeps" in tables:
        conn.execute("UPDATE memory_tidy_keeps SET status='refused', reason=? WHERE status IN ('queued','applying')",
                     (KEEP_REFUSED,))
    if "memory_tidy_cards" in tables:
        decided = "decided_at" if "decided_at" in _columns(conn, "memory_tidy_cards") else "decided"
        conn.execute(f"UPDATE memory_tidy_cards SET status='withdrawn', reason='retired', {decided}=? WHERE status='open'",
                     (when,))
    if "memory_tidy_passes" in tables:
        _close_passes(conn, tables, when)
    if "registry_metadata" in tables:
        _clear_settings(conn)


def _close_passes(conn, tables, when):
    rows = conn.execute("SELECT id, parent_task_id, check_task_id FROM memory_tidy_passes WHERE status='filed'").fetchall()
    for ident, parent, check in rows:
        if "tasks" in tables:
            for task_id in (check, parent):
                if task_id:
                    _close_task(conn, tables, task_id, when)
        conn.execute("UPDATE memory_tidy_passes SET status='skipped', reason='skipped: retired' WHERE id=?", (ident,))


def _close_task(conn, tables, task_id, when):
    row = conn.execute("SELECT status FROM tasks WHERE id=?", (task_id,)).fetchone()
    if row is None or row[0] not in ACTIVE:
        return
    bump = ", version=version+1" if "version" in _columns(conn, "tasks") else ""
    conn.execute(f"UPDATE tasks SET status='closed', closed_at=?, closed_by_id=?, updated_at=?, note=?{bump} WHERE id=?",
                 (when, KEEPER, when, NOTE, task_id))
    if "history_events" in tables:
        conn.execute("INSERT INTO history_events(id,subject_type,subject_id,actor_id,kind,old,new,note,action_id,via,data,"
                     "created_at) VALUES(?,?,?,?,?,?,?,?,NULL,NULL,NULL,?)",
                     (str(uuid.uuid4()), "task", task_id, KEEPER, "status", row[0], "closed", NOTE, when))


def _columns(conn, table):
    return {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}


def _clear_settings(conn):
    conn.execute("DELETE FROM registry_metadata WHERE key='memory_tidy:pilots'")
    row = conn.execute("SELECT value FROM registry_metadata WHERE key='system_routines'").fetchone()
    try:
        saved = json.loads(row[0]) if row else None
    except (TypeError, ValueError):
        return
    if isinstance(saved, dict) and "memory_tidy" in saved:
        saved.pop("memory_tidy")
        conn.execute("UPDATE registry_metadata SET value=? WHERE key='system_routines'", (json.dumps(saved, sort_keys=True),))
