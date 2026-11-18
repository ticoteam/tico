"""What a Tico Meet call shows on its stage (docs/meet.md, Sharing).

`calls.stage`: one record, JSON `{"kind": "screen"|"file", "file_id", "version", "by", "at", "n"}`; `{"n"}` alone once
stopped, NULL for a call never shared on. `n` counts its writes; the newest write replaces it. Checks first, so a second
run changes nothing.
"""


def _columns(conn, table):
    return {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}


def up(conn):
    if "stage" not in _columns(conn, "calls"):
        conn.execute("ALTER TABLE calls ADD COLUMN stage TEXT")
