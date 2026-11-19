"""Voice in Tico Meet calls (docs/meet.md, Voice): when End was pressed, and how long bots have spoken.

`calls.ending_at`: End stops listening and the bots' voice at once and marks the call ending; the call is `ended` once
the speech already heard is transcribed. `calls.voice_ms`: the bots' spoken time in the call, held to a cap. Each step
checks first, so a second run changes nothing.
"""

ADDS = (
    ("calls", "ending_at", "TEXT"),
    ("calls", "voice_ms", "INTEGER NOT NULL DEFAULT 0"),
)


def _columns(conn, table):
    return {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}


def up(conn):
    for table, column, kind in ADDS:
        if column not in _columns(conn, table):
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {kind}")
