"""Scheduled calls (docs/meet.md, Scheduled calls): a call booked for later, and the calendar event it owns.

`calls.scheduled_start` / `calls.scheduled_end`: when the call is booked for. `calls.prepped_at`: when its bots were
given their prep tasks, so the prep sweep gives them once. `calls.state` gains 'scheduled' and
`calendar_event_links.link_type` gains 'call'; both CHECKs are rebuilt with the table. Each step checks first, so a
second run changes nothing.
"""

import re

FOREIGN_KEYS_OFF = True

ADDS = (
    ("calls", "scheduled_start", "TEXT"),
    ("calls", "scheduled_end", "TEXT"),
    ("calls", "prepped_at", "TEXT"),
)

WIDEN = (
    ("calls", re.compile(r"(\bstate\s+IN\s*\()([^)]*)(\))", re.IGNORECASE), "scheduled"),
    ("calendar_event_links", re.compile(r"(\blink_type\s+IN\s*\()([^)]*)(\))", re.IGNORECASE), "call"),
)


def _columns(conn, table):
    return {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}


def _widen(conn, table, check, value):
    row = conn.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone()
    if row is None or not isinstance(row[0], str):
        raise RuntimeError(f"The scheduled-calls migration could not read the {table} table definition.")
    sql = row[0]
    match = check.search(sql)
    if match is None:
        raise RuntimeError(f"The scheduled-calls migration could not find the {table} CHECK to widen.")
    if f"'{value}'" in match[2]:
        return
    updated = sql[: match.start(2)] + match[2].rstrip() + f",'{value}'" + sql[match.end(2) :]
    updated = re.sub(rf"^CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?\"?{table}\"?", f"CREATE TABLE {table}_new", updated)
    columns = ", ".join(["rowid", *(f'"{row[1]}"' for row in conn.execute(f"PRAGMA table_info({table})"))])
    indexes = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='index' AND tbl_name=? AND sql IS NOT NULL", (table,)
    ).fetchall()
    # SQLite rechecks every trigger and view during the rename, including ones that use tables built after migrations.
    aside = conn.execute(
        "SELECT type, name, sql FROM sqlite_master WHERE type IN ('trigger', 'view') AND sql IS NOT NULL"
    ).fetchall()
    for kind, name, _ in aside:
        conn.execute(f'DROP {kind.upper()} IF EXISTS "{name}"')
    conn.execute(updated)
    conn.execute(f"INSERT INTO {table}_new ({columns}) SELECT {columns} FROM {table}")
    conn.execute(f"DROP TABLE {table}")
    conn.execute(f"ALTER TABLE {table}_new RENAME TO {table}")
    for (statement,) in indexes:
        conn.execute(statement)
    for _, _, statement in aside:
        conn.execute(statement)


def up(conn):
    for table, column, kind in ADDS:
        if column not in _columns(conn, table):
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {kind}")
    for table, check, value in WIDEN:
        _widen(conn, table, check, value)
