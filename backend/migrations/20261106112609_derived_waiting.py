"""Who a task waits on is derived, not stored (threads + tasks, step 6b): `tasks.waiting_on` goes.

A waiting task waits on the person who owns an open question task under it or blocking it (question_tasks.waiting_on).
Each open task with `waiting_on` set is copied into `task_waits_moved` first, and the server's next start turns each
into what the code now uses (question_tasks.move_waits): a person becomes an open question task for them with no
options, the task's waiting note as its question; a bot is the blocker the bot owns when the task has one (what
`--on bot:<slug>` always required), else a line on the task's note. A migration never calls app code, so making those
tasks waits for that start.

SQLite drops a column only when no index or trigger names it: the covering indexes are made again without it, the
partial index on it goes, and the triggers that name it (events.py's needs triggers) are dropped here and made again
without it at the next start. The other triggers step aside while the column goes, as in 20261105213649_task_readers.
"""
import re

ACTIVE = ("open", "doing", "waiting", "review", "ready")
INDEXES = (
    ("tasks_private_status", "tasks(id, requester_id, owner_id, readers, private, status) WHERE private IS NOT 0"),
    ("tasks_owner_status_cover", "tasks(owner_id, status, id, requester_id, private, readers)"),
    ("tasks_requester_status_cover", "tasks(requester_id, status, id, owner_id, private, readers)"),
)


def _columns(conn, table):
    return {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}


def up(conn):
    have = _columns(conn, "tasks")
    if "waiting_on" in have:
        staged = f"FROM tasks WHERE coalesce(waiting_on,'')<>'' AND status IN ({','.join('?' * len(ACTIVE))})"
        # Only a file with waits to move gets the staging table: a new file never has one, and move_waits drops it.
        if conn.execute(f"SELECT 1 {staged} LIMIT 1", ACTIVE).fetchone():
            conn.execute("CREATE TABLE IF NOT EXISTS task_waits_moved(task_id TEXT PRIMARY KEY, waiting_on TEXT NOT NULL, "
                         "created_at TEXT NOT NULL)")
            conn.execute("INSERT OR IGNORE INTO task_waits_moved(task_id, waiting_on, created_at) "
                         f"SELECT id, waiting_on, strftime('%Y-%m-%dT%H:%M:%fZ','now') {staged}", ACTIVE)
        for kind, name in conn.execute("SELECT type, name FROM sqlite_master WHERE tbl_name='tasks' "
                                       "AND type IN ('index','trigger') AND sql LIKE '%waiting_on%'").fetchall():
            conn.execute(f'DROP {kind.upper()} IF EXISTS "{name}"')
        # DROP COLUMN parses every trigger again, and on a new file an earlier migration's trigger names a table the
        # module code makes after the migrations: the triggers step aside for it.
        triggers = conn.execute("SELECT name, sql FROM sqlite_master WHERE type='trigger' AND sql IS NOT NULL").fetchall()
        for name, _ in triggers:
            conn.execute(f'DROP TRIGGER IF EXISTS "{name}"')
        conn.execute("ALTER TABLE tasks DROP COLUMN waiting_on")
        for _, sql in triggers:
            conn.execute(sql)
        have = _columns(conn, "tasks")
    for name, spec in INDEXES:
        # On a new file some of these columns come later, from the module code, which makes the same index then.
        if set(re.findall(r"\w+", spec[len("tasks("):spec.index(")")])) <= have:
            conn.execute(f"CREATE INDEX IF NOT EXISTS {name} ON {spec}")
