"""Related input (docs/pipelines-v3.md, "Related input"): a requirement kind, `related_input`, where Jev picks the one or
two bots a piece of work touches and each gives one round of comments (backend/related_input.py).

- `requirements` is rebuilt so its kind check allows `related_input`. Every other part of the table, its rows and its
  indexes are kept as they are: the kind list is read from the table and has the new kind added.
- `related_inputs`: one row per visit and requirement, owed until Jev scored the company's bots, then the picks, when they
  were asked, and when the requirement was met.

Running it again changes nothing.
"""

import re

FOREIGN_KEYS_OFF = True

KIND = "related_input"
# The kind's own check, not the one on person_id that also lists kinds.
KIND_CHECK = re.compile(r"(kind\s+TEXT\s+NOT\s+NULL\s+CHECK\s*\(\s*kind\s+IN\s*\()([^)]*)(\))", re.IGNORECASE)


def _aside(conn):
    """A rebuild re-checks every trigger and view, and on a new file a trigger can name a table the module code makes
    after the migrations. So they step aside, and go back as they were (`_back`)."""
    aside = conn.execute(
        "SELECT type, name, sql FROM sqlite_master WHERE type IN ('trigger', 'view') AND sql IS NOT NULL"
    ).fetchall()
    for kind, name, _sql in aside:
        conn.execute(f'DROP {kind.upper()} IF EXISTS "{name}"')
    return aside


def _back(conn, aside):
    for _kind, _name, sql in aside:
        conn.execute(sql)


def _rebuild(conn):
    found = conn.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='requirements'").fetchone()
    if not found:
        return
    sql = str(found[0])
    match = KIND_CHECK.search(sql)
    if not match or f"'{KIND}'" in match.group(2):
        return
    kinds = match.group(2).rstrip()
    new_sql = sql[: match.start(2)] + f"{kinds}, '{KIND}'" + sql[match.end(2) :]
    new_sql = re.sub(
        r"^\s*CREATE\s+TABLE\s+(IF\s+NOT\s+EXISTS\s+)?\"?requirements\"?", "CREATE TABLE requirements_new", new_sql
    )
    columns = ", ".join(f'"{row[1]}"' for row in conn.execute("PRAGMA table_info(requirements)"))
    indexes = [
        row[0]
        for row in conn.execute(
            "SELECT sql FROM sqlite_master WHERE type='index' AND tbl_name='requirements' AND sql IS NOT NULL"
        )
    ]
    aside = _aside(conn)
    conn.execute(new_sql)
    conn.execute(f"INSERT INTO requirements_new ({columns}) SELECT {columns} FROM requirements")
    conn.execute("DROP TABLE requirements")
    conn.execute("ALTER TABLE requirements_new RENAME TO requirements")
    for statement in indexes:
        conn.execute(statement)
    _back(conn, aside)


def up(conn):
    _rebuild(conn)
    conn.execute(
        "CREATE TABLE IF NOT EXISTS related_inputs ("
        "visit_id TEXT NOT NULL, requirement_id TEXT NOT NULL, task_id TEXT NOT NULL, owed_at TEXT NOT NULL, "
        "tries INTEGER NOT NULL DEFAULT 0, next_try_at TEXT, last_error TEXT, scored_at TEXT, outcome TEXT, "
        "scores TEXT CHECK (scores IS NULL OR json_valid(scores)), "
        "picks TEXT CHECK (picks IS NULL OR json_valid(picks)), asked_at TEXT, done_at TEXT, done_line TEXT, "
        "call_id TEXT, PRIMARY KEY (visit_id, requirement_id))"
    )
    conn.execute("CREATE INDEX IF NOT EXISTS related_inputs_open ON related_inputs(owed_at) WHERE done_at IS NULL")
    conn.execute("CREATE INDEX IF NOT EXISTS related_inputs_task ON related_inputs(task_id)")
