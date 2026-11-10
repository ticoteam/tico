"""Pipelines v3 cut-over (docs/dev/pipelines-v3-cutover.md): the old task engine's tables and columns go.

No task is carried over. A database that still has tasks was made by Tico 0.5, and this release does not open it:
`migrations.preflight` refuses it before anything is written, so that database still runs on 0.5, and this migration
refuses it too. Every company starts on a new database.

On a database with no tasks (a new one, where the numbered steps have just made the old shape):

1. `tasks` is made again with the columns the engine and the rest of Tico use. Gone: `type_id` and `step_id` with
   their foreign keys, `step_rank`, check-backs, question-task fields, next-run fields, verdicts, the blocked-time
   stamps (DEAD_COLUMNS). Its indexes and triggers go with the old table. The ones a migration made and that still
   mean something are made here; every other one is made again by the start-up code, after the migrations, as on any
   start.
2. `tasks_move_only` holds for every task: its status, stage, owner and pipeline change only while the engine's guard is
   open. `tasks_enter_only` is its twin for a new row: it starts open, in no pipeline and no stage, and
   `task_engine.enter` places it. `tasks_never_replaced` refuses an insert over a task that exists.
3. The old engine's tables are dropped (OLD_TABLES), children before parents.
4. The trash is emptied: a deleted task there has the old shape and could not be restored.

Foreign keys are off for the run (the runner checks none is left broken).
"""

FOREIGN_KEYS_OFF = True

DEAD_COLUMNS = (
    "type_id",
    "step_id",
    "step_rank",
    "check_back_at",
    "check_back_note",
    "questions",
    "question_topic",
    "if_no_answer",
    "review",
    "spend",
    "answer",
    "asked_at",
    "next_run",
    "carried_by",
    "resume",
    "verdict",
    "verdict_head",
    "building_started_at",
    "building_seconds",
    "blocker_key",
    "stale_asked_at",
)

TASKS = (
    "CREATE TABLE tasks ("
    "id TEXT PRIMARY KEY, title TEXT, body TEXT, requester_id TEXT, owner_id TEXT, "
    "status TEXT NOT NULL DEFAULT 'open', "
    "due_at TEXT, conversation_id TEXT, created_at TEXT, updated_at TEXT, done_at TEXT, closed_at TEXT, "
    "closed_by_id TEXT, note TEXT, goal_id TEXT, labels TEXT NOT NULL DEFAULT '[]', number INTEGER, "
    "private INTEGER NOT NULL DEFAULT 1 CHECK (private IN (0,1)), live_meeting_id TEXT, readers TEXT, "
    "work_revision INTEGER NOT NULL DEFAULT 0, pipeline_id TEXT REFERENCES pipelines(id), "
    "stage_id TEXT REFERENCES stages(id), assignee_id TEXT, lane TEXT NOT NULL DEFAULT 'company', rank REAL, "
    "occurrence TEXT, occurrence_step TEXT, rule_due_id TEXT, checkpoint TEXT, checkpoint_at TEXT, "
    "checkpoint_by_id TEXT, checkpoint_state TEXT, checkpoint_blocked INTEGER NOT NULL DEFAULT 0, "
    "visibility TEXT NOT NULL DEFAULT 'private' CHECK(visibility IN ('public','private')), "
    "public_title TEXT NOT NULL DEFAULT '', public_summary TEXT NOT NULL DEFAULT '', size REAL, request_id TEXT, "
    "version INTEGER NOT NULL DEFAULT 1, acceptance TEXT NOT NULL DEFAULT '[]')"
)

# Made by migrations, so made again here. The start-up code makes the rest.
KEPT = (
    "CREATE INDEX tasks_conversation ON tasks(conversation_id)",
    "CREATE UNIQUE INDEX tasks_number ON tasks(number)",
    "CREATE INDEX tasks_stage ON tasks(stage_id, status)",
    "CREATE TRIGGER tasks_body_work_revision AFTER UPDATE OF body ON tasks "
    "WHEN NEW.body IS NOT OLD.body "
    "BEGIN UPDATE tasks SET work_revision = work_revision + 1 WHERE id = NEW.id; END",
)

MOVE_ONLY = (
    "CREATE TRIGGER tasks_move_only "
    "BEFORE UPDATE OF status, stage_id, owner_id, pipeline_id ON tasks "
    "WHEN (SELECT task_id FROM task_move_guards WHERE id = 1 AND open = 1) IS NOT NEW.id "
    "BEGIN SELECT RAISE(ABORT, 'tasks: stage, owner and status change only through move()'); END"
)
ENTER_ONLY = (
    "CREATE TRIGGER tasks_enter_only BEFORE INSERT ON tasks "
    "WHEN NEW.pipeline_id IS NOT NULL OR NEW.stage_id IS NOT NULL OR NEW.status IS NOT 'open' "
    "BEGIN SELECT RAISE(ABORT, 'tasks: a new task starts open, in no stage; enter() places it'); END"
)

# A task is never replaced: INSERT OR REPLACE would take an entered task's row away and leave its visit behind.
NEVER_REPLACED = (
    "CREATE TRIGGER tasks_never_replaced BEFORE INSERT ON tasks "
    "WHEN EXISTS (SELECT 1 FROM tasks WHERE id = NEW.id) "
    "OR (NEW.number IS NOT NULL AND EXISTS (SELECT 1 FROM tasks WHERE number = NEW.number)) "
    "BEGIN SELECT RAISE(ABORT, 'tasks: a task is never replaced'); END"
)

# Children before parents. Each belonged to a module of the old engine; a table that is not there is skipped.
OLD_TABLES = (
    "task_check_back_wakes",
    "task_milestone_waits",
    "milestones",
    "check_waits",
    "stale_judged",
    "stale_suggestions",
    "stuck_alarms",
    "stuck_spells",
    "review_claims",
    "requirement_attachments",
    "pipeline_check_cursor",
    "pipeline_check_runs",
    "pipeline_handoffs",
    "pipeline_manager_actions",
    "pipeline_manager_runs",
    "pipeline_members",
    "pipeline_links",
    "stage_decisions",
    "stage_visits",
    "stage_requirement_versions",
    "stage_requirements",
    "task_holds",
    "approvals",
    "task_steps",
    "task_types",
)

# What one task leaves in the engine's own tables. All empty when there is no task; cleared so that is certain.
PER_TASK = ("effects", "answers", "links", "snoozes", "visits")


def _tables(conn):
    return {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}


def up(conn):
    tables = _tables(conn)
    if "tasks" not in tables:
        return
    held = conn.execute("SELECT COUNT(*) FROM tasks").fetchone()[0]
    if held:
        raise RuntimeError(
            f"This database has {held} tasks from Tico 0.5. Tico 0.6 does not carry tasks over: every company starts "
            "on a new database. Nothing was changed. Run Tico 0.5 on this database, or set up a new company on 0.6."
        )
    if "requirements" in tables:
        conn.execute("DELETE FROM requirements WHERE visit_id IS NOT NULL")
    for table in PER_TASK:
        if table in tables:
            conn.execute(f"DELETE FROM {table}")
    for table in ("task_trash_assets", "task_trash"):
        if table in tables:
            conn.execute(f"DELETE FROM {table}")
    conn.execute("DROP TABLE tasks")  # its indexes and triggers go with it
    conn.execute(TASKS)
    for statement in KEPT:
        conn.execute(statement)
    conn.execute(MOVE_ONLY)
    conn.execute(ENTER_ONLY)
    conn.execute(NEVER_REPLACED)
    for table in OLD_TABLES:
        conn.execute(f"DROP TABLE IF EXISTS {table}")
    # A factory's "output stage" named a stage of the old engine. The migration that added the column is retired
    # (migrations.SUPERSEDED), so a new database never has it; a database that ran it loses it here.
    if "factories" in tables and "output_stage_id" in {r[1] for r in conn.execute("PRAGMA table_info(factories)")}:
        conn.execute("ALTER TABLE factories DROP COLUMN output_stage_id")
