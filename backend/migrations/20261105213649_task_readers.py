"""A private task's one `guest` becomes the first entry of its reader list, `tasks.readers` (hubdb "a private task's
readers"), and the column goes. A deleted task's trash row keeps its readers the same way (backend/task_delete.py).

SQLite drops a column only when no index or trigger names it: the four covering indexes are made again with `readers`
in its place, and the triggers that name `guest` (events.py, row_clock.py) are dropped here and made again with
`readers` at the next start.
"""

INDEXES = (
    ("tasks_private_status", "tasks(id, requester_id, owner_id, readers, private, status, waiting_on) WHERE private IS NOT 0"),
    ("tasks_queue_cover", "tasks(rank, created_at, id, owner_id, requester_id, private, readers)"),
    ("tasks_owner_status_cover", "tasks(owner_id, status, id, requester_id, private, readers, waiting_on)"),
    ("tasks_requester_status_cover", "tasks(requester_id, status, id, owner_id, private, readers, waiting_on)"),
)


def _columns(conn, table):
    return {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}


def _move(conn, table):
    """Add `readers`, copy each guest into it, then drop `guest` with the indexes and triggers that name it."""
    columns = _columns(conn, table)
    if not columns:
        return
    if "readers" not in columns:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN readers TEXT")
    if "guest" not in columns:
        return
    conn.execute(f"UPDATE {table} SET readers=json_array(guest) WHERE guest IS NOT NULL AND guest<>'' "
                 "AND (readers IS NULL OR readers IN ('', '[]'))")
    for kind, name, sql in conn.execute("SELECT type, name, sql FROM sqlite_master WHERE tbl_name=? "
                                        "AND type IN ('index','trigger') AND sql LIKE '%guest%'", (table,)).fetchall():
        conn.execute(f"DROP {kind.upper()} IF EXISTS {name}")
    # DROP COLUMN parses every trigger again. On a new file an earlier migration's trigger names a table the module
    # code makes after the migrations (presences_meeting_delete names task_roles), so the triggers step aside for it.
    triggers = conn.execute("SELECT name, sql FROM sqlite_master WHERE type='trigger' AND sql IS NOT NULL").fetchall()
    for name, _ in triggers:
        conn.execute(f'DROP TRIGGER IF EXISTS "{name}"')
    conn.execute(f"ALTER TABLE {table} DROP COLUMN guest")
    for _, sql in triggers:
        conn.execute(sql)


def up(conn):
    _move(conn, "tasks")
    _move(conn, "task_trash")
    if {"private", "readers"} <= _columns(conn, "tasks"):
        for name, spec in INDEXES:
            conn.execute(f"CREATE INDEX IF NOT EXISTS {name} ON {spec}")
