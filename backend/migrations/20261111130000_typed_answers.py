"""Typed answers (docs/pipelines-v3.md, "Answer from someone"): a stage may ask for an answer, the task's owner's or a
named person's, with a name and a type (text, a whole number, or a choice).

- `requirements` is rebuilt without `CHECK (kind <> 'answer' OR visit_id IS NOT NULL)`, the rule that an answer is
  only ever asked of one task. Every row is kept as it is.
- `answers` gains `name` and `value_type`, copied from the requirement when the answer is written, and an index on
  `name` for the answers that have one. Existing answers keep both empty.
"""

FOREIGN_KEYS_OFF = True

KINDS = "('owner_done', 'ok', 'page', 'item', 'linked_done', 'answer')"
VALUE_TYPES = "('text', 'number', 'choice')"


def _tables(conn):
    return {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}


def _columns(conn, table):
    return {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}


def _aside(conn):
    """A rebuild or rename re-checks every trigger and view, and on a new file a trigger can name a table the module
    code makes after the migrations. So they step aside, and go back as they were (`_back`)."""
    aside = conn.execute(
        "SELECT type, name, sql FROM sqlite_master WHERE type IN ('trigger', 'view') AND sql IS NOT NULL"
    ).fetchall()
    for kind, name, _sql in aside:
        conn.execute(f'DROP {kind.upper()} IF EXISTS "{name}"')
    return aside


def _back(conn, aside):
    for _kind, _name, sql in aside:
        conn.execute(sql)


REQUIREMENTS = (
    "CREATE TABLE requirements_new ("
    "id TEXT PRIMARY KEY, stage_id TEXT REFERENCES stages(id), visit_id TEXT REFERENCES visits(id), "
    f"kind TEXT NOT NULL CHECK (kind IN {KINDS}), source TEXT, condition TEXT, "
    "settings TEXT NOT NULL DEFAULT '{}' CHECK (json_valid(settings)), "
    "can_push_past INTEGER NOT NULL DEFAULT 1 CHECK (can_push_past IN (0, 1)), "
    "who_oks TEXT NOT NULL DEFAULT 'none' CHECK (who_oks IN ('none', 'people_only', 'people_with_exceptions')), "
    "person_id TEXT, added_by_id TEXT, by_bot INTEGER NOT NULL DEFAULT 0 CHECK (by_bot IN (0, 1)), "
    "line TEXT NOT NULL DEFAULT '', shows TEXT CHECK (shows IS NULL OR json_valid(shows)), "
    "choices TEXT CHECK (choices IS NULL OR json_valid(choices)), position INTEGER NOT NULL DEFAULT 0, "
    "created_at TEXT NOT NULL, withdrawn_at TEXT, "
    "CHECK ((stage_id IS NULL) <> (visit_id IS NULL)), "
    "CHECK ((kind = 'item') = (source IS NOT NULL AND condition IS NOT NULL)), "
    "CHECK (person_id IS NULL OR kind IN ('ok', 'page', 'answer')))"
)

REQUIREMENT_INDEXES = (
    "CREATE INDEX IF NOT EXISTS requirements_stage ON requirements(stage_id, position) WHERE stage_id IS NOT NULL",
    "CREATE INDEX IF NOT EXISTS requirements_visit ON requirements(visit_id) WHERE visit_id IS NOT NULL",
    "CREATE INDEX IF NOT EXISTS requirements_person ON requirements(person_id) "
    "WHERE person_id IS NOT NULL AND withdrawn_at IS NULL",
    "CREATE UNIQUE INDEX IF NOT EXISTS requirements_one_per_bot ON requirements(visit_id, added_by_id) "
    "WHERE visit_id IS NOT NULL AND by_bot = 1 AND withdrawn_at IS NULL",
)
REQUIREMENT_COLUMNS = (
    "id, stage_id, visit_id, kind, source, condition, settings, can_push_past, who_oks, person_id, added_by_id, "
    "by_bot, line, shows, choices, position, created_at, withdrawn_at"
)


def _one_task_only(conn):
    """Whether `requirements` still says an answer is asked of one task only."""
    found = conn.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='requirements'").fetchone()
    return bool(found) and "kind <> 'answer'" in " ".join(str(found[0]).split())


def up(conn):
    tables = _tables(conn)
    rebuild = "requirements" in tables and _one_task_only(conn)
    add = "answers" in tables and not {"name", "value_type"} <= _columns(conn, "answers")
    if not rebuild and not add:
        return
    aside = _aside(conn)
    if rebuild:
        conn.execute(REQUIREMENTS)
        conn.execute(
            f"INSERT INTO requirements_new ({REQUIREMENT_COLUMNS}) SELECT {REQUIREMENT_COLUMNS} FROM requirements"
        )
        conn.execute("DROP TABLE requirements")
        conn.execute("ALTER TABLE requirements_new RENAME TO requirements")
        for statement in REQUIREMENT_INDEXES:
            conn.execute(statement)
    if "answers" in tables:
        columns = _columns(conn, "answers")
        if "name" not in columns:
            conn.execute("ALTER TABLE answers ADD COLUMN name TEXT")
        if "value_type" not in columns:
            conn.execute(
                "ALTER TABLE answers ADD COLUMN value_type TEXT "
                f"CHECK (value_type IS NULL OR value_type IN {VALUE_TYPES})"
            )
        conn.execute("CREATE INDEX IF NOT EXISTS answers_name ON answers(name, created_at) WHERE name IS NOT NULL")
    _back(conn, aside)
