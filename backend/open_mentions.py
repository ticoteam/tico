# pyright: strict
"""The one definition of an open @mention: a person's message that @s another person, or replies to one of their
messages, that they have not answered.

Needs you (views.needs_items) and Needs attention (thread_attention, the threads sidebar) both read it, so the sidebar
never flags a thread that Needs you does not list. The spec is docs/dev/pipelines-v3-cutover.md, "Needs you": every @ of
a person by a person, on a task or in a channel, until they reply. A person's reply to a message of the person's needs
them too (the Nov 6 reply_needs_you intent). A bot's message, an @ or a reply, only marks its thread unread: a bot that
needs a person marks its message ❓ (backend/question_marks.py), which is its own Needs-you item. A message addressed to a
person without an @ only marks it unread; a DM has its own item in Needs attention.

A mention is open while its delivery still waits on the person (pending, delivered or read: not moot after an edit or
a delete, not held or capped), its message is neither deleted nor superseded, its conversation is open, and the person
has posted nothing in that conversation since.
"""

import sqlite3
from collections.abc import Iterable
from typing import Any

from . import hubdb as H

# The delivery reasons that need the person: an @ of them by a person (threads' delivery trigger), and a person's reply to
# a message of theirs (threads' reply trigger). Every other reason only marks the thread unread.
MENTION = "mention"
REPLY = "reply"
# The delivery states that still wait on the person.
WAITING = ("pending", "delivered", "read")
# The partial index message_deliveries_addressed (migration 0056, redefined in 20261106040000) holds these reasons in these
# states. SQLite uses a partial index only when the query names its terms, so the query lists the index's reasons and
# narrows to the reasons that count (MENTION and REPLY, in the WHERE below): `reason = 'mention'` alone plans a scan of
# the person's every delivery (a follower's many follower rows), which is what the index exists to avoid.
INDEXED_REASONS = ("mention", "ask", "assign", "addressed", "reply")


def _in(values: Iterable[str]) -> str:
    return ",".join(f"'{v}'" for v in values)


# A reply to a message of the recipient's in the same thread root, as threads._replied_author reads it: the reply target's
# sender decides, not the delivery's winning reason (a reply that also addresses the person may keep another reason, since
# threads.plan ranks the reasons). A Tico message is read as the message it carries.
REPLIED_TO_RECIPIENT = (
    "(EXISTS (SELECT 1 FROM messages p JOIN conversations pc ON pc.id=p.conversation_id "
    "WHERE p.id=m.in_reply_to AND p.sender_id=d.recipient AND COALESCE(pc.root_id,pc.id)=COALESCE(cv.root_id,cv.id)) "
    "OR EXISTS (SELECT 1 FROM messages p JOIN messages k ON k.id=json_extract(CASE WHEN json_valid(p.refs) THEN p.refs END,'$.carries') "
    "JOIN conversations kc ON kc.id=k.conversation_id "
    f"WHERE p.id=m.in_reply_to AND p.sender_id='{H.TICO_ACTOR}' AND k.sender_id=d.recipient "
    "AND COALESCE(kc.root_id,kc.id)=COALESCE(cv.root_id,cv.id)))"
)


# Columns: the delivery's message, its conversation's root (the row a rail or a list stands for), the sender, the body,
# the conversation's subject, and the task the message is on (its conversation, or a task named in its refs). Both
# readers run this one statement.
SQL = (
    "SELECT m.id, m.conversation_id, coalesce(cv.root_id, cv.id) AS root_id, m.sender_id, m.body, m.created_at, "
    "cv.subject, coalesce(cv.task_id, json_extract(CASE WHEN json_valid(m.refs) THEN m.refs END, '$.task')) AS task_id "
    # Pin the partial index: the general recipient index would expand this to all of the person's deliveries. CROSS JOIN
    # keeps deliveries first whatever sqlite_stat1 says, then each message and conversation by key.
    "FROM message_deliveries d INDEXED BY message_deliveries_addressed "
    "CROSS JOIN messages m ON m.id=d.message_id CROSS JOIN conversations cv ON cv.id=m.conversation_id "
    f"WHERE d.recipient=? AND d.reason IN ({_in(INDEXED_REASONS)}) AND d.state IN ({_in(WAITING)}) "
    # Only a person's message: a bot's needs the person through its ❓ alone, never by an @ or a reply.
    "AND m.sender_id<>? AND m.sender_id LIKE 'human:%' "
    f"AND (d.reason IN ('{REPLY}','{MENTION}') OR {REPLIED_TO_RECIPIENT}) "
    f"AND {H.delivery_counts_sql('m', 'd.recipient', 'd.reason')} "
    "AND m.deleted_at IS NULL AND m.superseded_at IS NULL AND cv.closed_at IS NULL AND cv.archived_at IS NULL "
    "AND NOT EXISTS (SELECT 1 FROM messages r WHERE r.conversation_id=m.conversation_id AND r.sender_id=? "
    "AND r.created_at>m.created_at AND r.deleted_at IS NULL) "
    "ORDER BY m.created_at, m.id"
)


def rows(c: sqlite3.Connection, actor: str) -> list[dict[str, Any]]:
    """The open @mentions of `actor`, oldest first, as dicts of SQL's columns. Each reader still checks what the actor may
    read of each message: this list is the mentions, not the readable ones. Read once per snapshot (read_cache): Home
    asks it for Needs you and for the rail's attention list; each caller gets its own dicts."""
    from . import read_cache

    found = read_cache.value(
        c, ("open_mentions.rows", actor), lambda: tuple(c.execute(SQL, (actor, actor, actor))), True
    )
    return [dict(r) for r in found]
