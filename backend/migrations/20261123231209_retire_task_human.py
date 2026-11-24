"""A task's Human (`tasks.human_id`) is retired: a person assignee takes its place on the ladder (docs/tasks.md).

Each task's Human moves once. A company task with no assignee gets them as its assignee, so the OKs that name nobody
keep going to them (waiting_on.person_for: a person assignee is on the ladder after the holder); the move is written in
the task's history as the Human's own latest change was (same actor, Tico when none), so a Human a bot named still opens
no reach (reach.assignee_of). A company task whose assignee already is that person needs nothing. Every other Human goes
into the task's `human` role, a label.

The column and its index stay as they are: nothing reads or writes them after this, and a server rolled back to an
older release routes by the frozen value. The change triggers step aside while it runs (as in
20261117015544_task_prefix_and_moved_visits), so the live screens get no change row per task. Running it again changes
nothing.
"""

import uuid
from datetime import datetime, timezone

TICO = "keeper"  # hubdb_core.TICO_ACTOR
PUBLIC = "COALESCE(t.private, 1) = 0"
TO_ASSIGNEE = f"t.human_id IS NOT NULL AND t.assignee_id IS NULL AND {PUBLIC}"
TO_ROLE = f"t.human_id IS NOT NULL AND NOT ({PUBLIC} AND (t.assignee_id IS NULL OR t.assignee_id = t.human_id))"


def _aside(conn):
    aside = conn.execute(
        "SELECT type, name, sql FROM sqlite_master WHERE type IN ('trigger', 'view') AND sql IS NOT NULL"
    ).fetchall()
    for kind, name, _sql in aside:
        conn.execute(f'DROP {kind.upper()} IF EXISTS "{name}"')
    return aside


def _back(conn, aside):
    for _kind, _name, sql in aside:
        conn.execute(sql)


def up(conn):
    tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    if not {"tasks", "task_roles", "history_events"} <= tables:
        return
    have = {row[1] for row in conn.execute("PRAGMA table_info(tasks)")}
    if not {"human_id", "assignee_id", "private"} <= have:
        return
    at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
    aside = _aside(conn)
    conn.execute(
        "INSERT OR IGNORE INTO task_roles (task_id, role, actor, added_by, created_at) "
        f"SELECT t.id, 'human', t.human_id, ?, ? FROM tasks t WHERE {TO_ROLE}",
        (TICO, at),
    )
    for task_id, human in conn.execute(f"SELECT t.id, t.human_id FROM tasks t WHERE {TO_ASSIGNEE}").fetchall():
        named = conn.execute(
            "SELECT actor_id FROM history_events WHERE subject_type='task' AND subject_id=? AND kind='human_id' "
            "ORDER BY created_at DESC, rowid DESC LIMIT 1",
            (task_id,),
        ).fetchone()
        conn.execute(
            "INSERT INTO history_events (id, subject_type, subject_id, actor_id, kind, old, new, note, created_at) "
            "VALUES (?, 'task', ?, ?, 'assignee_id', NULL, ?, 'Its Human is its assignee now', ?)",
            (str(uuid.uuid4()), task_id, (named[0] if named and named[0] else TICO), human, at),
        )
        conn.execute("UPDATE tasks SET assignee_id=? WHERE id=? AND assignee_id IS NULL", (human, task_id))
    _back(conn, aside)
