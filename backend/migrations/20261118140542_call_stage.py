"""What a Tico Meet call shows on its stage (docs/meet.md, Sharing).

`calls.stage`: one record, JSON `{"kind": "screen"|"file", "file_id", "version", "by", "at", "n"}`; `{"n"}` alone once
stopped, NULL for a call never shared on. `n` counts its writes; the newest write replaces it. Checks first, so a second
run changes nothing.

An install may still have 20261118081646_call_voice pending, and pending files run in any order: this adds its columns
first, with the same definitions, so the columns end in one order whichever runs first (that migration skips a column
already there).
"""

ADDS = (
    ("ending_at", "TEXT"),
    ("voice_ms", "INTEGER NOT NULL DEFAULT 0"),
    ("stage", "TEXT"),
)


def up(conn):
    columns = {row[1] for row in conn.execute("PRAGMA table_info(calls)")}
    for column, kind in ADDS:
        if columns and column not in columns:
            conn.execute(f"ALTER TABLE calls ADD COLUMN {column} {kind}")
