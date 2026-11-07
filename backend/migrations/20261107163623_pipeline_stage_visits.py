"""Pipeline F0: stage_visits, one row for each stay a ticket has at a stage of its pipeline (the engine writes them from
Track A on). A ticket has at most one open visit (left_at NULL).

Backfill: every active ticket on a type with a current step gets one open visit at that step, entered when its latest
step event happened (its creation time when it has none). The visit is 'initial' and records the step it came from when
the event says so. A ticket that already has an open visit is skipped, so a second run adds nothing."""

import uuid

ACTIVE = ("open", "doing", "waiting", "review", "ready")
TABLE = """CREATE TABLE IF NOT EXISTS stage_visits (
    id TEXT PRIMARY KEY,
    task_id TEXT NOT NULL,
    pipeline_id TEXT NOT NULL,
    pipeline_stage_id TEXT NOT NULL,
    entered_from_stage_id TEXT,
    direction TEXT NOT NULL CHECK (direction IN ('initial','forward','back','handoff')),
    entered_by TEXT,
    entered_at TEXT NOT NULL,
    left_at TEXT
)"""
OPEN_INDEX = "CREATE UNIQUE INDEX IF NOT EXISTS stage_visits_open_task ON stage_visits(task_id) WHERE left_at IS NULL"
CURRENT = """SELECT t.id, t.type_id, t.step_id,
       CASE WHEN e.new = t.step_id THEN e.old END,
       CASE WHEN e.new = t.step_id THEN e.actor_id END,
       COALESCE(CASE WHEN e.new = t.step_id THEN e.created_at END, t.created_at)
FROM tasks t
LEFT JOIN (
    SELECT subject_id, actor_id, old, new, created_at,
           ROW_NUMBER() OVER (PARTITION BY subject_id ORDER BY created_at DESC, rowid DESC) AS rn
    FROM history_events
    WHERE subject_type = 'task' AND kind = 'step'
) e ON e.subject_id = t.id AND e.rn = 1
WHERE t.type_id IS NOT NULL AND t.step_id IS NOT NULL AND t.status IN ({active})
  AND NOT EXISTS (SELECT 1 FROM stage_visits v WHERE v.task_id = t.id AND v.left_at IS NULL)
""".format(active=", ".join("?" for _ in ACTIVE))


def up(conn):
    if not {row[1] for row in conn.execute("PRAGMA table_info(tasks)")}:
        return
    conn.execute(TABLE)
    conn.execute(OPEN_INDEX)
    if not conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='history_events'").fetchone():
        return
    for task_id, pipeline_id, stage_id, came_from, actor, entered_at in conn.execute(CURRENT, ACTIVE).fetchall():
        conn.execute(
            "INSERT INTO stage_visits (id, task_id, pipeline_id, pipeline_stage_id, entered_from_stage_id, direction, "
            "entered_by, entered_at, left_at) VALUES (?,?,?,?,?,'initial',?,?,NULL)",
            (str(uuid.uuid4()), task_id, pipeline_id, stage_id, came_from, actor, entered_at),
        )
