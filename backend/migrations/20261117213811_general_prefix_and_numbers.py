"""General gets a prefix, and its tasks a number (docs/tasks.md).

- General, the pipeline every task goes into unless it belongs to another, had no prefix, so none of its tasks had a
  number and nobody could write "#123" for one. It takes G now, or, where another pipeline in use already has G, the
  first letter from A to Z no pipeline in use has. A General that has a prefix keeps it.
- Every General task with no number, open, done or closed, gets one, oldest first, after the team's highest number
  (in `tasks` and in the bin, `task_trash`, whose numbers are never given again). A number already given never
  changes. A task whose own letters are empty (made while General had none) takes General's.
- Each numbered task has one history row ("Numbered when General got the prefix G"), written as the keeper; nothing is
  woken or sent.

A new company has no General yet when this runs: `pipeline_settings.ensure_presets` makes it with G. New General tasks
get their number when they are made (`hubdb._next_number`), and one restored from the bin when it comes back.

Running it again changes nothing: General then has a prefix.
"""

import string
import uuid
from datetime import datetime, timezone

KEEPER = "keeper"
LIMIT = 999_999_999


def _now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _tables(conn):
    return {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}


def _letter(conn):
    taken = {
        row[0] for row in conn.execute("SELECT prefix FROM pipelines WHERE retired_at IS NULL AND prefix IS NOT NULL")
    }
    return next((one for one in "G" + string.ascii_uppercase if one not in taken), None)


def _top(conn, trash):
    top = conn.execute("SELECT COALESCE(MAX(number), 0) FROM tasks").fetchone()[0]
    if trash:
        top = max(top, conn.execute("SELECT COALESCE(MAX(number), 0) FROM task_trash").fetchone()[0])
    return int(top)


def up(conn):
    tables = _tables(conn)
    if not {"pipelines", "tasks", "history_events"} <= tables:
        return
    general = conn.execute(
        "SELECT id, name FROM pipelines WHERE (id='general' OR preset='general') AND retired_at IS NULL "
        "AND COALESCE(prefix, '')='' ORDER BY id='general' DESC LIMIT 1"
    ).fetchone()
    if not general:
        return
    letter = _letter(conn)
    if not letter:
        return
    pipeline_id, name = general
    conn.execute("UPDATE pipelines SET prefix=? WHERE id=?", (letter, pipeline_id))
    note = f"Numbered when {name} got the prefix {letter}"
    number = _top(conn, "task_trash" in tables)
    ts = _now()
    letters = "prefix" in {row[1] for row in conn.execute("PRAGMA table_info(tasks)")}
    for (task_id,) in conn.execute(
        "SELECT id FROM tasks WHERE pipeline_id=? AND number IS NULL ORDER BY created_at, id", (pipeline_id,)
    ).fetchall():
        if number >= LIMIT:
            break
        number += 1
        if letters:  # one write per task: each fires the live screens' change trigger
            conn.execute(
                "UPDATE tasks SET number=?, prefix=CASE WHEN COALESCE(prefix, '')='' THEN ? ELSE prefix END WHERE id=?",
                (number, letter, task_id),
            )
        else:
            conn.execute("UPDATE tasks SET number=? WHERE id=?", (number, task_id))
        conn.execute(
            "INSERT INTO history_events (id, subject_type, subject_id, actor_id, kind, old, new, note, created_at) "
            "VALUES (?, 'task', ?, ?, 'number', NULL, ?, ?, ?)",
            (str(uuid.uuid4()), task_id, KEEPER, str(number), note, ts),
        )
