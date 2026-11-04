"""A message whose task reference is a list is read under the list rule, so the decisions made before are made again.

Until this change a list in refs.task (or refs.task_id) matched no reader: the SQL gates and ReadIndex read a scalar, so a
message naming a list of tasks was settled as if it named none. read_index keeps those decisions, and the sweep walks
only rows it does not find there, so they would stay wrong. This moves each such message back to the queue the way a
change to it would: its read_index row goes, and it is queued in read_dirty. Every row that read it (read_deps dep
'm:<id>'), and every row that read those, is queued the same way, because its decision read the old answer.

Only a message with a JSON array at refs.task or refs.task_id is requeued; a scalar reference, or refs that are not JSON,
is left alone. It writes no DDL and only read_index and read_dirty rows. Running it again queues the same rows again, so
it is safe to repeat. Without these tables (a fresh install) there is nothing to requeue.
"""

STEP = 500
LISTED = ("instr(refs,'task')>0 AND CASE WHEN json_valid(refs) THEN "
          "(json_type(refs,'$.task')='array' OR json_type(refs,'$.task_id')='array') ELSE 0 END")


def _chunks(items):
    for start in range(0, len(items), STEP):
        yield items[start:start + STEP]


def _marks(items):
    return ",".join("?" * len(items))


def up(conn):
    tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    if not {"messages", "read_index", "read_dirty", "read_deps"} <= tables:
        return
    keys = {"m:" + row[0] for row in conn.execute(f"SELECT id FROM messages WHERE {LISTED}")}
    # The rows that read a requeued message, and the rows that read those, until no new row turns up.
    frontier = set(keys)
    while frontier:
        found = set()
        for chunk in _chunks(sorted(frontier)):
            found.update(row[0] for row in conn.execute(
                f"SELECT key FROM read_deps WHERE dep IN ({_marks(chunk)})", chunk))
        frontier = found - keys
        keys |= frontier
    for chunk in _chunks(sorted(keys)):
        conn.execute(f"DELETE FROM read_index WHERE key IN ({_marks(chunk)})", chunk)
    conn.executemany("INSERT OR IGNORE INTO read_dirty(key) VALUES(?)", [(key,) for key in sorted(keys)])
