# pyright: strict
"""Another agent's answer to what the person's Assistant asked it for them comes back to the person's Assistant chat.

When the Assistant writes to an agent for the person (a delegated send: `hubdb.VIA` is "assistant"; a say or a steer, not
an FYI notice), `_write_message` records that the person is waiting on that agent in that thread (`awaiting_replies`, a
server-only table: no client reads or sets it). The agent's first say or steer in that thread, or in any thread under
it, consumes (deletes) every such wait there in one statement, in its own transaction, whether or not it is ever
delivered. Only then, just before that write commits (`flush`, from store.Store.transaction), each
person waiting is told once in their Assistant chat: "<agent> replied", with a link that opens the reply, when they can
read the reply as it will commit; a reply they cannot read is not told at all. Each delivery is best-effort in its own
savepoint: a failure is logged on the server, never touches the reply and never reopens a wait.
"""

import logging
import sqlite3
from typing import Any, cast

from . import task_effects, task_requirements

Row = dict[str, Any]
LOG = logging.getLogger("tico.assistant_relay")
# Every wait on this agent in the reply's thread or one above it (awaiting_replies_agent), consumed at once.
_CONSUME_SQL = (
    "DELETE FROM awaiting_replies WHERE agent_id=? AND root_id=? AND thread_id IN ({threads}) RETURNING person_id"
)


def asked(conn: sqlite3.Connection, message_id: str, person: str, agent: str, conv: Row | None) -> None:
    """The Assistant wrote `message_id` to `agent` for `person` in `conv`: they now wait on its first reply there."""
    if not conv:
        return
    from . import hubdb

    H: Any = hubdb
    conn.execute(
        "INSERT INTO awaiting_replies(message_id, person_id, agent_id, root_id, thread_id, created_at) "
        "VALUES (?,?,?,?,?,?)",
        (message_id, person, agent, str(conv.get("root_id") or conv["id"]), str(conv["id"]), H.now()),
    )


def thread_ancestors(conv: Row) -> list[str]:
    """The thread itself and every thread above it, from its stored path."""
    root = str(conv.get("root_id") or conv["id"])
    return [part for part in str(conv.get("path") or "").split("/") if part] or [root, str(conv["id"])]


def replied(conn: sqlite3.Connection, reply: Row, conv: Row | None) -> None:
    """An agent wrote `reply` in `conv`: it consumes every wait on that agent in this thread or one above it, and each
    person waiting is told once, before the write commits (at once on a connection outside a store transaction)."""
    agent = str(reply.get("sender_id") or "")
    slug = str(getattr(getattr(conn, "settings", None), "assistant_bot", "") or "assistant")
    if agent == "bot:" + slug or not conv:
        return
    root = str(conv.get("root_id") or conv["id"])
    # The reply's thread and every thread above it (its path, "/<root>/.../<id>/"): a wait in a sibling is not answered.
    above = thread_ancestors(conv)
    try:
        people = [
            str(row[0])
            for row in conn.execute(
                _CONSUME_SQL.format(threads=",".join("?" * len(above))), (agent, root, *above)
            ).fetchall()
        ]
    except sqlite3.Error:
        LOG.exception("assistant relay could not consume the waits for message %s", reply.get("id"))
        return
    if not people:
        return
    owed = (str(reply["id"]), list(dict.fromkeys(people)))
    pending: object = getattr(conn, "relay_pending", None)
    if isinstance(pending, list):
        cast(list[object], pending).append(owed)
    else:
        _deliver(conn, *owed)


def flush(conn: Any) -> None:
    """Deliver the relays this write owes, on each reply as it will commit."""
    pending: object = getattr(conn, "relay_pending", None)
    if not isinstance(pending, list):
        return
    owed = cast(list[tuple[str, list[str]]], pending)
    while owed:
        _deliver(conn, *owed.pop(0))


def _deliver(conn: sqlite3.Connection, reply_id: str, people: list[str]) -> None:
    from . import hubdb

    H: Any = hubdb
    reply: Row | None = H.message(conn, reply_id)
    if not reply:
        return
    for person in people:
        # Best-effort and server-side only: a failure is logged and rolled back to here, and nothing else records it.
        conn.execute("SAVEPOINT assistant_relay")
        try:
            if task_requirements.active_person(conn, person):
                _tell(conn, reply, person)
        except Exception:  # one person's relay never touches the reply or the next person's
            conn.execute("ROLLBACK TO assistant_relay")
            LOG.exception("assistant relay failed for message %s", reply_id)
        finally:
            conn.execute("RELEASE assistant_relay")


def _reads(conn: sqlite3.Connection, person: str, reply: Row) -> bool:
    """Whether `person` reads the reply now, by the read check GET /api/v2/messages/{id} makes (thread_api), through
    the database's own authorization (recipients._recipient_auth): a right taken away since the ask counts."""
    from . import recipients, thread_api
    from .store import Problem

    gate: Any = recipients
    route: Any = thread_api
    auth = gate._recipient_auth(conn)
    try:
        route.require_message_read(conn, auth, auth.identity_for_actor(conn, person), reply)
    except Problem:
        return False
    return True


def _tell(conn: sqlite3.Connection, reply: Row, person: str) -> None:
    from . import hubdb

    H: Any = hubdb
    if not _reads(conn, person, reply):
        return  # a reply they cannot read is not told at all: hidden and absent look the same
    key = f"assistant_relay:{reply['id']}:{person}"
    if conn.execute("SELECT 1 FROM messages WHERE wake_key=?", (key,)).fetchone():
        return  # told already: one relay per reply per person
    name = task_effects.plain_line(H.actor_name(conn, reply["sender_id"]), "An agent")
    # The agent's chat, where the reply is its newest message.
    body = f"{name} replied\n\n[Open the reply](#/thread/{reply['conversation_id']})"
    line: Row = {"assistant": True, "quiet": True, "no_mentions": True}
    task_effects.assistant_line(conn, person, body, line, key)
