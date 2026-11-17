"""Moving a task to another pipeline (docs/pipelines-v3.md, "The moves": Move to another pipeline).

- `tasks.prefix`: the letters of the task's label, kept on the task from the pipeline it was made in, so a task that
  moves keeps its label (D18450 stays D18450 on another board). Every task made before has its pipeline's prefix now.
- `visits` is rebuilt so a visit can end, and the next one start, `moved`: the task went to a stage of another pipeline.
  Every other part of the table, its rows and its indexes are kept as they are: the two lists of ways are read from the
  table and have `moved` added.

Running it again changes nothing.
"""

import re

FOREIGN_KEYS_OFF = True

WAY = "moved"
# The two checks that list how a visit starts and ends. `\bhow` is not the end of `entered_how`.
CHECKS = (
    re.compile(r"(\bentered_how\s+IN\s*\()([^)]*)(\))", re.IGNORECASE),
    re.compile(r"(\bhow\s+IN\s*\()([^)]*)(\))", re.IGNORECASE),
)


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


def _rebuild_visits(conn):
    found = conn.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='visits'").fetchone()
    if not found:
        return
    sql = new_sql = str(found[0])
    for check in CHECKS:
        match = check.search(new_sql)
        if match and f"'{WAY}'" not in match.group(2):
            new_sql = new_sql[: match.start(2)] + f"{match.group(2).rstrip()}, '{WAY}'" + new_sql[match.end(2) :]
    if new_sql == sql:
        return
    new_sql = re.sub(r"^\s*CREATE\s+TABLE\s+(IF\s+NOT\s+EXISTS\s+)?\"?visits\"?", "CREATE TABLE visits_new", new_sql)
    columns = ", ".join(f'"{row[1]}"' for row in conn.execute("PRAGMA table_info(visits)"))
    indexes = [
        row[0]
        for row in conn.execute(
            "SELECT sql FROM sqlite_master WHERE type='index' AND tbl_name='visits' AND sql IS NOT NULL"
        )
    ]
    conn.execute(new_sql)
    conn.execute(f"INSERT INTO visits_new ({columns}) SELECT {columns} FROM visits")
    conn.execute("DROP TABLE visits")
    conn.execute("ALTER TABLE visits_new RENAME TO visits")
    for statement in indexes:
        conn.execute(statement)


def _prefix(conn):
    have = {row[1] for row in conn.execute("PRAGMA table_info(tasks)")}
    if not have:
        return
    if "prefix" not in have:
        conn.execute("ALTER TABLE tasks ADD COLUMN prefix TEXT")
    conn.execute(
        "UPDATE tasks SET prefix=(SELECT p.prefix FROM pipelines p WHERE p.id = tasks.pipeline_id) "
        "WHERE prefix IS NULL AND pipeline_id IS NOT NULL"
    )


def up(conn):
    # With the triggers aside the backfill writes no change row per task for the live screens: nothing they show moved.
    aside = _aside(conn)
    _rebuild_visits(conn)
    _prefix(conn)
    _back(conn, aside)
