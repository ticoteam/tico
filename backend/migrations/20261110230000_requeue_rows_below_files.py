"""Rows whose provenance reads a message naming a file are read again by the walk, not from the stored index.

A reply now inherits the file refusals of the messages above it, and a run those of its messages (backend/task_privacy.py
`reply_pages_readable`). A file refusal is per reader, so the settle walk no longer settles a row that read a message
naming a file (read_deps.names_file). read_index keeps the decisions made before, and the sweep walks only rows it
does not find there, so this moves every row that read such a message back to the queue, the way a change to that
message would: its read_index row goes and it is queued in read_dirty, with every row that read those rows.

It writes no DDL and only read_index and read_dirty rows. Running it again queues the same rows again, so it is safe
to repeat. Without these tables (a fresh install) there is nothing to requeue.
"""

STEP = 500
PAGE_KEYS = ("target", "answer", "review", "body_sources")  # task_privacy.PAGE_MESSAGE_KEYS on 2026-11-10


def _names_file(refs):
    """read_deps.names_file, frozen here: any key's quoted name, or an escape."""
    return bool(refs) and refs != "{}" and ("\\" in refs or any(f'"{key}"' in refs for key in PAGE_KEYS))


def _chunks(items):
    for start in range(0, len(items), STEP):
        yield items[start : start + STEP]


def _marks(items):
    return ",".join("?" * len(items))


def up(conn):
    tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    if not {"messages", "read_index", "read_dirty", "read_deps"} <= tables:
        return
    named = {
        "m:" + mid
        for mid, refs in conn.execute("SELECT id, refs FROM messages WHERE refs IS NOT NULL AND refs NOT IN ('', '{}')")
        if isinstance(refs, str) and _names_file(refs)
    }
    # The rows that read such a message, and the rows that read those, until no new row turns up.
    keys, frontier = set(), set(named)
    while frontier:
        found = set()
        for chunk in _chunks(sorted(frontier)):
            found.update(
                row[0] for row in conn.execute(f"SELECT key FROM read_deps WHERE dep IN ({_marks(chunk)})", chunk)
            )
        frontier = found - keys
        keys |= frontier
    for chunk in _chunks(sorted(keys)):
        conn.execute(f"DELETE FROM read_index WHERE key IN ({_marks(chunk)})", chunk)
    conn.executemany("INSERT OR IGNORE INTO read_dirty(key) VALUES(?)", [(key,) for key in sorted(keys)])
