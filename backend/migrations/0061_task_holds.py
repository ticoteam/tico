"""A task's holds are rows of `task_holds`, not columns of `tasks`.

A task had two holds, each a set of columns on its row: a snooze (`snoozed_until`, `snoozed_by_id`, `snoozed_note`)
and a move to Blocked (`blocked_since`, `blocked_reason`, `blocked_kind`, `blocked_from_step`, `blocked_from_status`,
`blocked_gate`, and `holds_cleared_at`, the stranded sweep's clock). Each open hold becomes one task_holds row:

- kind 'snoozed': until_at = snoozed_until, by_id = snoozed_by_id, reason = snoozed_note. Its created_at is when the
  snooze was set (the latest `snoozed_until` task event), else the task's updated_at.
- kind 'blocked': created_at = blocked_since, reason = blocked_reason, cause = blocked_kind (manual, dependency,
  stranded, gate, check, worktree), from_step_id = blocked_from_step, from_status = blocked_from_status,
  gate = blocked_gate, cleared_at = holds_cleared_at.

A task has at most one open hold of each kind (the partial unique index); a hold that ends gets released_at and stays
as history. `blocker_key` stays on tasks: it is blocker_asks' match key for an ask, not a hold.

The columns, their indexes and the status trigger on blocked_from_status go. On a new file the columns were never
added (hubdb and task_blocked.ensure added them after the migrations), so only the table is made.
"""

TABLE = """CREATE TABLE IF NOT EXISTS task_holds(
 id INTEGER PRIMARY KEY, task_id TEXT NOT NULL REFERENCES tasks(id),
 kind TEXT NOT NULL CHECK (kind IN ('blocked','snoozed')),
 cause TEXT, reason TEXT, by_id TEXT, until_at TEXT,
 from_step_id TEXT, from_status TEXT, gate TEXT, cleared_at TEXT,
 created_at TEXT NOT NULL, released_at TEXT)"""
INDEXES = (
    "CREATE UNIQUE INDEX IF NOT EXISTS task_holds_open ON task_holds(task_id, kind) WHERE released_at IS NULL",
    "CREATE INDEX IF NOT EXISTS task_holds_kind ON task_holds(kind, created_at) WHERE released_at IS NULL",
    "CREATE INDEX IF NOT EXISTS task_holds_until ON task_holds(until_at) WHERE released_at IS NULL AND kind='snoozed'",
)
COLUMNS = ("snoozed_until", "snoozed_by_id", "snoozed_note", "blocked_since", "blocked_reason", "blocked_kind",
           "blocked_from_step", "blocked_from_status", "blocked_gate", "holds_cleared_at")


def up(conn):
    conn.execute(TABLE)
    for statement in INDEXES:
        conn.execute(statement)
    have = {row[1] for row in conn.execute("PRAGMA table_info(tasks)")}
    if not have & set(COLUMNS):
        return

    def col(name):
        return f"t.{name}" if name in have else "NULL"

    if "snoozed_until" in have:
        conn.execute(
            "INSERT INTO task_holds(task_id, kind, reason, by_id, until_at, created_at) "
            f"SELECT t.id, 'snoozed', {col('snoozed_note')}, {col('snoozed_by_id')}, t.snoozed_until, "
            "coalesce((SELECT max(e.created_at) FROM task_events e WHERE e.task_id=t.id AND e.field='snoozed_until' "
            "AND e.new IS NOT NULL), t.updated_at, t.created_at, '') "
            "FROM tasks t WHERE t.snoozed_until IS NOT NULL "
            "AND NOT EXISTS (SELECT 1 FROM task_holds h WHERE h.task_id=t.id AND h.kind='snoozed' "
            "AND h.released_at IS NULL)")
    if "blocked_since" in have:
        conn.execute(
            "INSERT INTO task_holds(task_id, kind, cause, reason, from_step_id, from_status, gate, cleared_at, "
            "created_at) "
            f"SELECT t.id, 'blocked', {col('blocked_kind')}, {col('blocked_reason')}, {col('blocked_from_step')}, "
            f"{col('blocked_from_status')}, {col('blocked_gate')}, {col('holds_cleared_at')}, t.blocked_since "
            "FROM tasks t WHERE t.blocked_since IS NOT NULL "
            "AND NOT EXISTS (SELECT 1 FROM task_holds h WHERE h.task_id=t.id AND h.kind='blocked' "
            "AND h.released_at IS NULL)")
    expected = sum(conn.execute(f"SELECT count(*) FROM tasks WHERE {c} IS NOT NULL").fetchone()[0]
                   for c in ("snoozed_until", "blocked_since") if c in have)
    moved = conn.execute("SELECT count(*) FROM task_holds WHERE released_at IS NULL").fetchone()[0]
    if moved != expected:
        raise RuntimeError(f"task_holds: {moved} open holds for {expected} held tasks")
    for name in ("tasks_snoozed", "tasks_blocked_since"):
        conn.execute(f"DROP INDEX IF EXISTS {name}")
    for name in ("ins", "upd"):
        conn.execute(f"DROP TRIGGER IF EXISTS status_values_tasks_blocked_from_status_{name}")
    for name in COLUMNS:
        if name in have:
            conn.execute(f"ALTER TABLE tasks DROP COLUMN {name}")
