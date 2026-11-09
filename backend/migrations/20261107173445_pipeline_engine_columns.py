"""Pipeline engine, Track A: the columns and small tables the engine, the links and the Tico Team setup read.

- task_types.goal: one sentence on what the pipeline is for (shown under its name, given to its manager and judge).
- task_types.judge_model: the model that judges this pipeline's requirements; NULL is the default (Track B).
- task_types.pipeline_visibility: 'company' (everyone who may see tickets) or 'members' (only its owner, manager and
  pipeline_members). Not `visibility`: tasks has a column of that name, so a tasks/task_types join would be ambiguous.
- factories.goal and factories.manager_id: what the factory is for and the bot that manages it.
- pipeline_members: who a members-only pipeline shows to besides its owner and manager.
- Indexes for the handoff counts the flow map reads (pipeline_handoffs was created empty by F0).
- tasks.work_revision moves up by one whenever a ticket's body changes: a body edit is a work change, so evidence
  attached before it is out of date. A trigger, because every writer of the body goes through it. A push to a linked
  pull request bumps it in backend/github.py through pipeline_engine.work_changed, and an attachment that counts as work
  through the same function.

Every statement is guarded, so a second run changes nothing."""


def _columns(conn, table):
    return {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}


def _add(conn, table, column, definition):
    columns = _columns(conn, table)
    if columns and column not in columns:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")


def up(conn):
    _add(conn, "task_types", "goal", "TEXT")
    _add(conn, "task_types", "judge_model", "TEXT")
    _add(
        conn,
        "task_types",
        "pipeline_visibility",
        "TEXT NOT NULL DEFAULT 'company' CHECK (pipeline_visibility IN ('company','members'))",
    )
    _add(conn, "factories", "goal", "TEXT")
    _add(conn, "factories", "manager_id", "TEXT")
    conn.execute(
        "CREATE TABLE IF NOT EXISTS pipeline_members ("
        "pipeline_id TEXT NOT NULL, actor_id TEXT NOT NULL, created_at TEXT NOT NULL, "
        "PRIMARY KEY (pipeline_id, actor_id))"
    )
    if _columns(conn, "pipeline_handoffs"):
        conn.execute("CREATE INDEX IF NOT EXISTS pipeline_handoffs_source ON pipeline_handoffs(source_task_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS pipeline_handoffs_target ON pipeline_handoffs(target_task_id)")
        conn.execute(
            "CREATE INDEX IF NOT EXISTS pipeline_handoffs_link ON pipeline_handoffs(pipeline_link_id, created_at)"
        )
    if "work_revision" in _columns(conn, "tasks"):
        conn.execute(
            "CREATE TRIGGER IF NOT EXISTS tasks_body_work_revision AFTER UPDATE OF body ON tasks "
            "WHEN NEW.body IS NOT OLD.body "
            "BEGIN UPDATE tasks SET work_revision = work_revision + 1 WHERE id = NEW.id; END"
        )
