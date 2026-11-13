"""Each Google connect or disconnect gets its own operation id (`google_connects.operation_id`), and the result its
computer answered for it (`answered`). The claim hands the id to the computer and `/exchange` and `/result` are
addressed by it, so a late answer from an earlier connect can no longer finish a later one (backend/google_connect.py).

A row from before this has no id: it gets a fresh one here, so a queued connect or disconnect is still claimed. A claim
handed out before this named the person instead, and its `/exchange` or `/result` is now refused; the row expires as
`unknown` and the person connects again. A new file has no table yet: the module's CREATE carries both columns.
"""


def up(conn):
    columns = {row[1] for row in conn.execute("PRAGMA table_info(google_connects)")}
    if not columns:
        return
    for column in ("operation_id", "answered"):
        if column not in columns:
            conn.execute(f"ALTER TABLE google_connects ADD COLUMN {column} TEXT")
    conn.execute("UPDATE google_connects SET operation_id='gc_'||lower(hex(randomblob(16))) WHERE operation_id IS NULL")
