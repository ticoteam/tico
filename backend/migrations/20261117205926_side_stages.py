"""Keep side stages in a pipeline's stage order and record moves through them on visits."""

import re

FOREIGN_KEYS_OFF = True

_CHECKS = {
    "entered_how": re.compile(r"(\bentered_how\s+IN\s*\()([^)]*)(\))", re.IGNORECASE),
    "how": re.compile(r"(\bhow\s+IN\s*\()([^)]*)(\))", re.IGNORECASE),
}


def up(conn):
    if "side" not in {row[1] for row in conn.execute("PRAGMA table_info(stages)")}:
        conn.execute("ALTER TABLE stages ADD COLUMN side INTEGER NOT NULL DEFAULT 0 CHECK (side IN (0,1))")

    table = conn.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='visits'").fetchone()
    if table is None or not isinstance(table[0], str):
        raise RuntimeError("The side-stage migration could not read the visits table definition.")
    sql = table[0]
    updated = sql
    for column, check in _CHECKS.items():
        match = check.search(updated)
        if match is None:
            raise RuntimeError(f"The side-stage migration could not find the visits CHECK for {column}.")
        if "'side'" not in match[2]:
            updated = updated[: match.start(2)] + f"{match[2].rstrip()}, 'side'" + updated[match.end(2) :]
    if updated == sql:
        return
    updated = re.sub(r"^CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?\"?visits\"?", "CREATE TABLE visits_new", updated)
    columns = ", ".join(['rowid', *(f'"{r[1]}"' for r in conn.execute("PRAGMA table_info(visits)"))])
    indexes = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='index' AND tbl_name='visits' AND sql IS NOT NULL"
    ).fetchall()
    # SQLite rechecks every trigger and view during the rename, including ones that use tables built after migrations.
    aside = conn.execute(
        "SELECT type, name, sql FROM sqlite_master WHERE type IN ('trigger', 'view') AND sql IS NOT NULL"
    ).fetchall()
    for kind, name, _ in aside:
        conn.execute(f'DROP {kind.upper()} IF EXISTS "{name}"')
    conn.execute(updated)
    conn.execute(f"INSERT INTO visits_new ({columns}) SELECT {columns} FROM visits")
    conn.execute("DROP TABLE visits")
    conn.execute("ALTER TABLE visits_new RENAME TO visits")
    for (statement,) in indexes:
        conn.execute(statement)
    for _, _, statement in aside:
        conn.execute(statement)
