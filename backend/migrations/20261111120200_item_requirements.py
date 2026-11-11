"""Requirement kinds become six (docs/pipelines-v3.md "The six requirement kinds"): owner_done, ok, page, item,
linked_done, answer. An `item` requirement names its `source` and `condition`; GitHub's conditions are pr_open,
checks_pass, pr_merged and in_release, the four kinds they were. Rows are kept, those four turned into `item`.
"""

FOREIGN_KEYS_OFF = True

KINDS = "('owner_done', 'ok', 'page', 'item', 'linked_done', 'answer')"
GITHUB_KINDS = ("pr_open", "checks_pass", "pr_merged", "in_release")


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
    "CHECK (kind <> 'answer' OR visit_id IS NOT NULL), "
    "CHECK ((kind = 'item') = (source IS NOT NULL AND condition IS NOT NULL)), "
    "CHECK (person_id IS NULL OR kind IN ('ok', 'page', 'answer')))",
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
    "id, stage_id, visit_id, settings, can_push_past, who_oks, person_id, added_by_id, by_bot, line, shows, choices, "
    "position, created_at, withdrawn_at"
)


def up(conn):
    if "requirements" not in _tables(conn) or "condition" in _columns(conn, "requirements"):
        return
    aside = _aside(conn)
    for statement in REQUIREMENTS:
        conn.execute(statement)
    github = ", ".join(f"'{kind}'" for kind in GITHUB_KINDS)
    conn.execute(
        f"INSERT INTO requirements_new (kind, source, condition, {REQUIREMENT_COLUMNS}) "
        f"SELECT CASE WHEN kind IN ({github}) THEN 'item' ELSE kind END, "
        f"CASE WHEN kind IN ({github}) THEN 'github' END, CASE WHEN kind IN ({github}) THEN kind END, "
        f"{REQUIREMENT_COLUMNS} FROM requirements"
    )
    conn.execute("DROP TABLE requirements")
    conn.execute("ALTER TABLE requirements_new RENAME TO requirements")
    for statement in REQUIREMENT_INDEXES:
        conn.execute(statement)
    _back(conn, aside)
