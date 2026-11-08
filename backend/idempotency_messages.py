"""Which stored retry answers (the `idempotency` table) quote which message, so a comment edit or delete finds them
by index instead of reading every stored answer while it holds the write lock.

A retry gets back the answer its first try got. When a comment is edited or deleted, `hubdb._refresh_comment_replies`
rewrites every stored answer that quotes it, so a retry never replays withdrawn words. A quoted message is any JSON
object in the answer with an `id` and a `sender_id` key (`from_actor` in an answer stored before that rename): the objects that function rewrites. Reading every answer for
the comment's id took 35-37 s inside the write transaction on a 665k-row table (Dev 194), and every other writer
waited behind it. So each answer's quoted message ids are kept here:

- triggers keep `idempotency_messages` in step with every insert, update and delete on `idempotency`, whoever writes
  it (`Store.mutate`, credentials, the GitHub app, the retention sweep);
- rows an older server wrote are filled in beside the running server (`backfill_store`, started with it), in short
  batches, reading outside the write lock; until that first pass has finished (`READY`), an edit reads the answers the
  old way. On 665k synthetic answers it took about 26 s; the server does not wait for it to start.
"""
import json
import sqlite3

READY = "idempotency_messages.ready"
BATCH = 500     # answers indexed per write transaction: about 0.15 s of lock on 665k synthetic answers
SPAN = 20000    # rowids the backfill reads per look, outside the write lock

# The ids of the objects with both an `id` (a string) and a sender key in one answer, in one pass over its JSON.
# Object members carry their label in `key`, array elements their index, so `key='id'` is always an object's member.
# An answer SQLite does not read as JSON (none is written so: answers are served as JSON) is kept under UNREAD, and
# `quoting` matches those by text, as `_refresh_comment_replies` always did.
UNREAD = "*"
_MAY_QUOTE = "(instr({src}, '\"sender_id\"')>0 OR instr({src}, '\"from_actor\"')>0 OR NOT json_valid({src}))"


def _quoted(row, where=""):
    """SELECT (message_id, actor, operation, key) for the answers `row` names: `NEW` in a trigger, or `i` with `where`
    choosing rows of `idempotency i`. One query for both, so the backfill indexes exactly what the triggers do."""
    text, source = f"{row}.response", f"idempotency {row} WHERE {where} AND" if where else "WHERE"
    return (f"SELECT id, actor, operation, key FROM (SELECT {row}.actor AS actor, {row}.operation AS operation, "
            f"{row}.key AS key, max(CASE WHEN t.key='id' AND t.type='text' THEN t.value END) AS id, "
            f"max(t.key IN ('sender_id','from_actor')) AS sender FROM {f'idempotency {row}, ' if where else ''}"
            f"json_tree(CASE WHEN json_valid({text}) THEN {text} ELSE 'null' END) t "
            f"WHERE {f'{where} AND ' if where else ''}t.key IN ('id','sender_id','from_actor') "
            f"GROUP BY {f'{row}.rowid, ' if where else ''}t.parent) WHERE sender AND id IS NOT NULL "
            f"UNION SELECT '{UNREAD}', {row}.actor, {row}.operation, {row}.key "
            f"{'FROM ' if where else ''}{source} NOT json_valid({text})")


SCHEMA = (
    "CREATE TABLE IF NOT EXISTS idempotency_messages(message_id TEXT NOT NULL, actor TEXT NOT NULL, "
    "operation TEXT NOT NULL, key TEXT NOT NULL, PRIMARY KEY(message_id, actor, operation, key)) WITHOUT ROWID",
    "CREATE INDEX IF NOT EXISTS idempotency_messages_answer ON idempotency_messages(actor, operation, key)",
    "CREATE TRIGGER IF NOT EXISTS idempotency_messages_insert AFTER INSERT ON idempotency "
    f"WHEN {_MAY_QUOTE.format(src='NEW.response')} BEGIN "
    f"INSERT OR IGNORE INTO idempotency_messages {_quoted('NEW')}; END",
    "CREATE TRIGGER IF NOT EXISTS idempotency_messages_update AFTER UPDATE ON idempotency BEGIN "
    "DELETE FROM idempotency_messages WHERE actor=OLD.actor AND operation=OLD.operation AND key=OLD.key; "
    f"INSERT OR IGNORE INTO idempotency_messages {_quoted('NEW')}; END",
    "CREATE TRIGGER IF NOT EXISTS idempotency_messages_delete AFTER DELETE ON idempotency BEGIN "
    "DELETE FROM idempotency_messages WHERE actor=OLD.actor AND operation=OLD.operation AND key=OLD.key; END",
)


def ensure(conn):
    """Table and triggers; idempotent. A file with no stored answers yet has nothing to backfill, so it is ready."""
    fresh = not conn.execute("SELECT 1 FROM sqlite_master WHERE name='idempotency_messages'").fetchone()
    for statement in SCHEMA:
        conn.execute(statement)
    if fresh and not conn.execute("SELECT 1 FROM idempotency LIMIT 1").fetchone():
        _set(conn, READY, True)


def quoting(conn, message_id):
    """(rowid, response) of every stored answer that quotes `message_id`, by index; None until the backfill has
    finished, when only reading every answer finds them all."""
    if not _get(conn, READY):
        return None
    return conn.execute("SELECT i.rowid, i.response FROM idempotency_messages m JOIN idempotency i "
                        "ON i.actor=m.actor AND i.operation=m.operation AND i.key=m.key WHERE m.message_id=? "
                        "OR (m.message_id=? AND instr(i.response, ?)>0)",
                        (message_id, UNREAD, message_id)).fetchall()


def backfill_store(store, stop):
    """`backfill` on its own connection, beside the server, until it finishes once or the server stops."""
    try:
        with store.read() as conn:
            backfill(conn, stop=stop)
    except Exception as exc:                # the next start tries again; edits read every answer meanwhile
        import logging
        logging.getLogger("tico.idempotency").error("Indexing stored answers failed: %s", type(exc).__name__)


def backfill(conn, batch=BATCH, span=SPAN, stop=None):
    """The quoted ids of every answer written before the triggers existed. Runs at every start until it has finished
    once; the search reads `span` rowids at a time outside the write lock, and each `batch` of answers that may quote
    a message is indexed in its own short write transaction. Answers written meanwhile are indexed by the triggers.
    Idempotent; a pass that is stopped (checked before each write, the ready mark included) starts again at the next start. Returns how many
    answers it read."""
    if _get(conn, READY):
        return 0
    done, after = 0, 0
    top = conn.execute("SELECT coalesce(max(rowid), 0) FROM idempotency").fetchone()[0]
    def stopped():
        return stop is not None and stop.is_set()
    while after < top:
        if stopped():
            return done
        rowids = [r[0] for r in conn.execute(
            f"SELECT rowid FROM idempotency WHERE rowid>? AND rowid<=? AND {_MAY_QUOTE.format(src='response')}",
            (after, after + span))]
        after += span
        for start in range(0, len(rowids), batch):
            if stopped():                   # between batches too: shutdown waits for this thread
                return done + start
            chunk = rowids[start:start + batch]
            chosen = f"i.rowid IN ({','.join('?' * len(chunk))})"
            _write(conn, lambda: conn.execute(f"INSERT OR IGNORE INTO idempotency_messages {_quoted('i', chosen)}",
                                              chunk * 2))
        done += len(rowids)
    if stopped():                           # every batch written, but ready is left to a pass that ends unstopped
        return done
    _write(conn, lambda: _set(conn, READY, True))
    return done


def _write(conn, work):
    from .flight import begin
    done = begin(conn)
    try:
        work()
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise
    finally:
        done()


def _get(conn, key):
    try:
        row = conn.execute("SELECT value FROM registry_metadata WHERE key=?", (key,)).fetchone()
    except sqlite3.OperationalError:
        return None
    return json.loads(row[0]) if row else None


def _set(conn, key, value):
    conn.execute("CREATE TABLE IF NOT EXISTS registry_metadata(key TEXT PRIMARY KEY, value TEXT NOT NULL)")
    conn.execute("INSERT INTO registry_metadata VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                 (key, json.dumps(value)))
