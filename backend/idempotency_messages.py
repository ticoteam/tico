"""Which stored retry answers (the `idempotency` table, binding NULL) quote which message, so a comment edit or delete
finds them by index instead of reading every stored answer while it holds the write lock. Operation bindings (binding
set: backend/invites.py, backend/github_app.py, backend/github_reviews.py) are not answers and are never indexed.

A retry gets back the answer its first try got. When a comment is edited or deleted, `hubdb._refresh_comment_replies`
rewrites every stored answer that quotes it, so a retry never replays withdrawn words. A quoted message is any JSON
object in the answer with an `id` and a `sender_id` key (`from_actor` in an answer stored before that rename): the objects that function rewrites. Reading every answer for
the comment's id took 35-37 s inside the write transaction on a 665k-row table (Dev 194), and every other writer
waited behind it. So each answer's quoted message ids are kept here:
triggers keep `idempotency_messages` in step with every insert, update and delete on `idempotency`, whoever writes it
(`Store.mutate`, credentials, the retention sweep); an insert of a binding is not indexed. Every database this code opens
has had them from its start, or had its older answers indexed by a release before the compatibility floor.
"""

# An answer SQLite does not read as JSON (none is written so: answers are served as JSON) is kept under UNREAD, and
# `quoting` matches those by text.
UNREAD = "*"


def quoting(conn, message_id):
    """(rowid, response) of every stored answer that quotes `message_id`, by index."""
    return conn.execute(
        "SELECT i.rowid, i.response FROM idempotency_messages m JOIN idempotency i "
        "ON i.actor=m.actor AND i.operation=m.operation AND i.key=m.key WHERE i.binding IS NULL "
        "AND (m.message_id=? OR (m.message_id=? AND instr(i.response, ?)>0))",
        (message_id, UNREAD, message_id),
    ).fetchall()
