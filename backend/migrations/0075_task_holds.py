"""A task's holds are rows of `task_holds`, not columns of `tasks`.

A task had two holds, each a set of columns on its row: a snooze (`snoozed_until`, `snoozed_by_id`, `snoozed_note`)
and a move to Blocked (`blocked_since`, `blocked_reason`, `blocked_kind`, `blocked_from_step`, `blocked_from_status`,
`blocked_gate`, and `holds_cleared_at`, the stranded sweep's clock). Each open hold becomes one task_holds row:

- kind 'snoozed': until_at = snoozed_until, by_id = snoozed_by_id, reason = snoozed_note. Its created_at is when the
  snooze was set (the latest `snoozed_until` history event, else the task's updated_at).
- kind 'blocked': created_at = blocked_since, reason = blocked_reason, cause = blocked_kind (manual, dependency,
  stranded, gate, check, worktree), from_step_id = blocked_from_step, from_status = blocked_from_status,
  gate = blocked_gate, cleared_at = holds_cleared_at.

A task has at most one open hold of each kind (the partial unique index); a hold that ends gets released_at and stays
as history. `blocker_key` stays on tasks: it is blocker_asks' match key for an ask, not a hold.

The steps, each one safe to run again:
- the table and its indexes (hubdb._holds_table made them before the migration files ran, so the CREATE may find them);
- the run-record repair for migration 0072 (below);
- the copy of each kind, when the file still has its column, and a count: the open rows of a kind must be as many as the
  tasks with that column set, or the migration stops (the whole migration is one transaction, so nothing is kept);
- `tasks_snoozed` and `tasks_blocked_since`, and the status trigger on blocked_from_status: SQLite refuses to drop a
  column an index or trigger names, so these go first (IF EXISTS);
- each of the ten columns, ALTER TABLE ... DROP COLUMN, when the file has it. No table rebuild: the only schema objects
  naming these columns are the four above, so the foreign keys into tasks are left as they are.

Run-record repair: migration 0072 moved a turn saved with no trigger onto its attempts row with a NULL trigger. A run
record is told from a bare lease by its trigger (`job_id IS NULL OR trigger IS NOT NULL`, hubdb TURN_SELECT), so such a
run that had a lease dropped out of the run list. turn_start always writes a trigger (its default, "message"), so a row
that has a record (settled, an exit, a summary, an outcome or usage) and no trigger gets that default. A bare lease that
failed before its record has none of those fields and stays NULL. 0072 itself is not edited: it has shipped.
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
# The indexes and triggers that name one of COLUMNS. Each is dropped IF EXISTS: a file that lacks one has nothing to do.
OLD_INDEXES = ("tasks_snoozed", "tasks_blocked_since")
OLD_TRIGGERS = ("status_values_tasks_blocked_from_status_ins", "status_values_tasks_blocked_from_status_upd")
# turn_start's default trigger (backend/hubdb.py), and the attempts columns a run record's own fields are on
# (backend/migrations/0072_turns_into_attempts.py).
RUN_TRIGGER = "message"
RECORD_FIELDS = ("settled_at", "exit", "summary", "outcome", "input_tokens")


def _columns(conn, table):
    """The table's column names; empty when the table is not there."""
    return {row[1] for row in conn.execute(f'PRAGMA table_info("{table}")')}


def _repair_run_records(conn):
    if not {"trigger", *RECORD_FIELDS} <= _columns(conn, "attempts"):
        return
    has_record = " OR ".join(f"{name} IS NOT NULL" for name in RECORD_FIELDS)
    conn.execute(f"UPDATE attempts SET trigger=? WHERE trigger IS NULL AND ({has_record})", (RUN_TRIGGER,))


def _copy(conn, have):
    def col(name):
        return f"t.{name}" if name in have else "NULL"

    if "snoozed_until" in have:
        conn.execute(
            "INSERT INTO task_holds(task_id, kind, reason, by_id, until_at, created_at) "
            f"SELECT t.id, 'snoozed', {col('snoozed_note')}, {col('snoozed_by_id')}, t.snoozed_until, "
            "coalesce((SELECT max(e.created_at) FROM history_events e WHERE e.subject_type='task' AND e.subject_id=t.id "
            "AND e.kind='snoozed_until' AND e.new IS NOT NULL), t.updated_at, t.created_at, '') "
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


def _check(conn, have):
    kinds = (("snoozed", "snoozed_until"), ("blocked", "blocked_since"))
    expected = sum(conn.execute(f"SELECT count(*) FROM tasks WHERE {column} IS NOT NULL").fetchone()[0]
                   for _, column in kinds if column in have)
    # Counted against the columns: a file hubdb's steps ran on before this made task_holds early (hubdb._holds_table)
    # may hold rows those steps wrote for tasks with no hold column set.
    moved = sum(conn.execute(
        "SELECT count(*) FROM task_holds h JOIN tasks t ON t.id=h.task_id "
        f"WHERE h.released_at IS NULL AND h.kind=? AND t.{column} IS NOT NULL", (kind,)).fetchone()[0]
        for kind, column in kinds if column in have)
    if moved != expected:
        raise RuntimeError(f"task_holds: {moved} open holds for {expected} held tasks")


def up(conn):
    conn.execute(TABLE)
    for statement in INDEXES:
        conn.execute(statement)
    _repair_run_records(conn)
    have = _columns(conn, "tasks")
    if have & set(COLUMNS):
        _copy(conn, have)
        _check(conn, have)
    for name in OLD_INDEXES:
        conn.execute(f"DROP INDEX IF EXISTS {name}")
    for name in OLD_TRIGGERS:
        conn.execute(f"DROP TRIGGER IF EXISTS {name}")
    for name in COLUMNS:
        if name in have:
            conn.execute(f"ALTER TABLE tasks DROP COLUMN {name}")
