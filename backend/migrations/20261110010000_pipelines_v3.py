"""Pipelines v3 (docs/pipelines-v3.md, docs/dev/pipelines-v3-build.md): the tables of the one task engine.

- pipelines, stages, requirements: what a pipeline is. A requirement sits on a stage (every task) or on a visit (this
  task, this time).
- visits: one row per stay of a task in a stage. Exactly one is open per task. It carries the never-stuck check's
  ladder (tries, level, next_try_at) and the Late marks.
- answers: what met a requirement for a visit, or pushed past it.
- snoozes, links, effects: a task set aside, a task filed by a task, a GitHub call still owed.
- task_move_guards: the one row the trigger reads. `task_engine` opens it for one task and closes it again inside its
  write.
- tasks.pipeline_id, stage_id, assignee_id; messages.wake_key (a wake written twice is one wake).
- Trigger tasks_move_only: a task on the engine (pipeline_id set) changes status, stage, owner or pipeline only while
  the guard is open.

Nothing is backfilled. A task made before this has pipeline_id NULL and stays on the old engine until the cut-over.
Every step checks first, so a second run changes nothing.
"""

TABLES = (
    "CREATE TABLE IF NOT EXISTS pipelines ("
    "id TEXT PRIMARY KEY, name TEXT NOT NULL COLLATE NOCASE, goal TEXT NOT NULL DEFAULT '', prefix TEXT, "
    "manager_id TEXT, owner_id TEXT, private INTEGER NOT NULL DEFAULT 0 CHECK (private IN (0, 1)), "
    "factory_id TEXT, preset TEXT, settings_version INTEGER NOT NULL DEFAULT 1, "
    "created_at TEXT NOT NULL, updated_at TEXT NOT NULL, retired_at TEXT)",
    "CREATE UNIQUE INDEX IF NOT EXISTS pipelines_name ON pipelines(name) WHERE retired_at IS NULL",
    "CREATE UNIQUE INDEX IF NOT EXISTS pipelines_prefix ON pipelines(prefix) "
    "WHERE prefix IS NOT NULL AND retired_at IS NULL",
    "CREATE TABLE IF NOT EXISTS stages ("
    "id TEXT PRIMARY KEY, pipeline_id TEXT NOT NULL REFERENCES pipelines(id), position INTEGER NOT NULL, "
    "kind TEXT NOT NULL CHECK (kind IN ('work', 'done', 'closed')), name TEXT NOT NULL, "
    "purpose TEXT NOT NULL DEFAULT '', owner TEXT, by_hand INTEGER NOT NULL DEFAULT 0 CHECK (by_hand IN (0, 1)), "
    "task_limit INTEGER CHECK (task_limit IS NULL OR task_limit > 0), "
    "expected_minutes INTEGER CHECK (expected_minutes IS NULL OR expected_minutes > 0), "
    "expected_bots_only INTEGER NOT NULL DEFAULT 0 CHECK (expected_bots_only IN (0, 1)), "
    "on_enter TEXT NOT NULL DEFAULT '{}' CHECK (json_valid(on_enter)), "
    "created_at TEXT NOT NULL, updated_at TEXT NOT NULL, retired_at TEXT)",
    "CREATE UNIQUE INDEX IF NOT EXISTS stages_name ON stages(pipeline_id, name COLLATE NOCASE) WHERE retired_at IS NULL",
    "CREATE INDEX IF NOT EXISTS stages_pipeline ON stages(pipeline_id, position)",
    "CREATE TABLE IF NOT EXISTS visits ("
    "id TEXT PRIMARY KEY, task_id TEXT NOT NULL, stage_id TEXT NOT NULL REFERENCES stages(id), "
    "entered_at TEXT NOT NULL, "
    "entered_how TEXT NOT NULL CHECK (entered_how IN ('new', 'met', 'pushed_past', 'back', 'closed', 'reopened')), "
    "left_at TEXT, how TEXT CHECK (how IS NULL OR how IN ('met', 'pushed_past', 'back', 'closed', 'reopened')), "
    "by_id TEXT, why TEXT, tries INTEGER NOT NULL DEFAULT 0, "
    "level TEXT NOT NULL DEFAULT 'owner' CHECK (level IN ('owner', 'manager', 'person')), person_id TEXT, "
    "next_try_at TEXT, progress_mark TEXT, last_change_at TEXT, late_told_at TEXT, late_person_at TEXT, "
    "bot_snoozes INTEGER NOT NULL DEFAULT 0, CHECK ((left_at IS NULL) = (how IS NULL)))",
    "CREATE UNIQUE INDEX IF NOT EXISTS visits_open ON visits(task_id) WHERE left_at IS NULL",
    "CREATE INDEX IF NOT EXISTS visits_task ON visits(task_id, entered_at)",
    "CREATE INDEX IF NOT EXISTS visits_stage_open ON visits(stage_id) WHERE left_at IS NULL",
    "CREATE TABLE IF NOT EXISTS requirements ("
    "id TEXT PRIMARY KEY, stage_id TEXT REFERENCES stages(id), visit_id TEXT REFERENCES visits(id), "
    "kind TEXT NOT NULL CHECK (kind IN ('owner_done', 'ok', 'page', 'pr_open', 'checks_pass', 'pr_merged', "
    "'in_release', 'linked_done', 'answer')), "
    "settings TEXT NOT NULL DEFAULT '{}' CHECK (json_valid(settings)), "
    "can_push_past INTEGER NOT NULL DEFAULT 1 CHECK (can_push_past IN (0, 1)), "
    "who_oks TEXT NOT NULL DEFAULT 'none' CHECK (who_oks IN ('none', 'people_only', 'people_with_exceptions')), "
    "person_id TEXT, added_by_id TEXT, by_bot INTEGER NOT NULL DEFAULT 0 CHECK (by_bot IN (0, 1)), "
    "line TEXT NOT NULL DEFAULT '', shows TEXT CHECK (shows IS NULL OR json_valid(shows)), "
    "choices TEXT CHECK (choices IS NULL OR json_valid(choices)), position INTEGER NOT NULL DEFAULT 0, "
    "created_at TEXT NOT NULL, withdrawn_at TEXT, "
    "CHECK ((stage_id IS NULL) <> (visit_id IS NULL)), "
    "CHECK (kind <> 'answer' OR visit_id IS NOT NULL), "
    "CHECK (person_id IS NULL OR kind IN ('ok', 'page', 'answer')))",
    "CREATE INDEX IF NOT EXISTS requirements_stage ON requirements(stage_id, position) WHERE stage_id IS NOT NULL",
    "CREATE INDEX IF NOT EXISTS requirements_visit ON requirements(visit_id) WHERE visit_id IS NOT NULL",
    "CREATE INDEX IF NOT EXISTS requirements_person ON requirements(person_id) "
    "WHERE person_id IS NOT NULL AND withdrawn_at IS NULL",
    "CREATE UNIQUE INDEX IF NOT EXISTS requirements_one_per_bot ON requirements(visit_id, added_by_id) "
    "WHERE visit_id IS NOT NULL AND by_bot = 1 AND withdrawn_at IS NULL",
    "CREATE TABLE IF NOT EXISTS answers ("
    "id TEXT PRIMARY KEY, visit_id TEXT NOT NULL REFERENCES visits(id), "
    "requirement_id TEXT NOT NULL REFERENCES requirements(id), by_id TEXT NOT NULL, answer TEXT NOT NULL, "
    "notes TEXT, page_version INTEGER, created_at TEXT NOT NULL, undone_at TEXT)",
    "CREATE INDEX IF NOT EXISTS answers_visit ON answers(visit_id, requirement_id, created_at)",
    "CREATE TABLE IF NOT EXISTS snoozes ("
    "id TEXT PRIMARY KEY, task_id TEXT NOT NULL, visit_id TEXT, by_id TEXT NOT NULL, why TEXT, until_at TEXT, "
    "created_at TEXT NOT NULL, ended_at TEXT, ended_by_id TEXT)",
    "CREATE UNIQUE INDEX IF NOT EXISTS snoozes_open ON snoozes(task_id) WHERE ended_at IS NULL",
    "CREATE INDEX IF NOT EXISTS snoozes_task ON snoozes(task_id, created_at)",
    "CREATE TABLE IF NOT EXISTS links ("
    "id TEXT PRIMARY KEY, from_task_id TEXT NOT NULL, to_task_id TEXT NOT NULL, visit_id TEXT, "
    "kind TEXT NOT NULL CHECK (kind IN ('sub_task', 'handoff')), created_at TEXT NOT NULL, "
    "CHECK (from_task_id <> to_task_id))",
    "CREATE UNIQUE INDEX IF NOT EXISTS links_to ON links(to_task_id)",
    "CREATE INDEX IF NOT EXISTS links_from ON links(from_task_id)",
    "CREATE INDEX IF NOT EXISTS links_visit ON links(visit_id) WHERE visit_id IS NOT NULL",
    "CREATE TABLE IF NOT EXISTS effects ("
    "id TEXT PRIMARY KEY, visit_id TEXT NOT NULL REFERENCES visits(id), "
    "kind TEXT NOT NULL CHECK (kind IN ('queue_pr', 'dequeue_pr')), key TEXT NOT NULL UNIQUE, "
    "state TEXT NOT NULL DEFAULT 'owed' CHECK (state IN ('owed', 'done', 'failed', 'moot')), "
    "tries INTEGER NOT NULL DEFAULT 0, last_error TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL)",
    "CREATE INDEX IF NOT EXISTS effects_owed ON effects(state, updated_at) WHERE state = 'owed'",
    "CREATE TABLE IF NOT EXISTS task_move_guards ("
    "id INTEGER PRIMARY KEY CHECK (id = 1), open INTEGER NOT NULL DEFAULT 0 CHECK (open IN (0, 1)), task_id TEXT)",
    "INSERT OR IGNORE INTO task_move_guards (id, open) VALUES (1, 0)",
)

TRIGGER = (
    "CREATE TRIGGER IF NOT EXISTS tasks_move_only "
    "BEFORE UPDATE OF status, stage_id, owner_id, pipeline_id ON tasks "
    "WHEN (OLD.pipeline_id IS NOT NULL OR NEW.pipeline_id IS NOT NULL) "
    "AND (SELECT task_id FROM task_move_guards WHERE id = 1 AND open = 1) IS NOT NEW.id "
    "BEGIN SELECT RAISE(ABORT, 'tasks: stage, owner and status change only through move()'); END"
)


def _columns(conn, table):
    return {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}


def up(conn):
    for statement in TABLES:
        conn.execute(statement)
    tasks = _columns(conn, "tasks")
    for column in ("pipeline_id", "stage_id", "assignee_id"):
        if tasks and column not in tasks:
            conn.execute(f"ALTER TABLE tasks ADD COLUMN {column} TEXT")
    if tasks:
        conn.execute("CREATE INDEX IF NOT EXISTS tasks_stage ON tasks(stage_id, status) WHERE pipeline_id IS NOT NULL")
        conn.execute(TRIGGER)
    messages = _columns(conn, "messages")
    if messages and "wake_key" not in messages:
        conn.execute("ALTER TABLE messages ADD COLUMN wake_key TEXT")
    if messages:
        conn.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS messages_wake_key ON messages(wake_key) WHERE wake_key IS NOT NULL"
        )
