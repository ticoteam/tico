"""The task-thread plain rule's property, checked after every test of the privacy suite (conftest `plain_messages_read_as_
their_thread`): each message in a task's own thread that the test left plain (restricted 0) answers the per-message
check, for every human and bot, as its task does (task_privacy.readable on {that task}), which is what the fast paths
count it by once the thread check passes."""

from backend import task_privacy


def mismatches(store):
    found = []
    with store.read_transaction() as c:
        rows = c.execute(
            "SELECT m.*, t.task_id AS thread_task FROM messages m JOIN conversations t ON t.id=m.conversation_id "
            "WHERE m.restricted=0 AND m.deleted_at IS NULL AND m.legacy_audience IS NULL AND t.task_id IS NOT NULL "
            "AND COALESCE(t.audience,'')<>'meeting' ORDER BY m.rowid"
        ).fetchall()
        if not rows:
            return found
        readers = [
            r[0] for r in c.execute("SELECT id FROM principals WHERE kind IN ('human','bot') AND deleted_at IS NULL")
        ]
        for row in map(dict, rows):
            task = row.pop("thread_task")
            for reader in readers:
                if task_privacy.message_readable(c, reader, row) != task_privacy.readable(c, reader, {task}):
                    found.append((reader, row["id"], row["body"][:60]))
    return found
