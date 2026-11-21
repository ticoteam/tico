# pyright: strict
"""Another agent's answer to what the person's Assistant asked it for them comes back to the person's Assistant chat.

When the Assistant writes to an agent for the person (a delegated send: `hubdb.VIA` is "assistant"), `_write_message`
puts a server-owned marker on that message, `refs.awaiting_reply` {person, agent, root}: no writer can set it. The first
message that agent then writes in that thread, or in any of its sub-threads, consumes every such marker there at once, in
its own transaction (its id goes on each as `reply_id`, whether or not it is ever delivered). Just before that write
commits (`flush`, from store.Store.transaction), each person waiting on it is told once in their Assistant chat:
"<agent> replied", with a link that opens the reply. Only a plain reply in the person's own chat with that agent, which
they read, carries a short excerpt, decided on the message as it will commit. Each delivery is best-effort in its own
savepoint: a failure is logged, never touches the reply and never reopens a marker.
"""

import logging
import sqlite3
from typing import Any, cast

from . import task_effects, task_requirements

Row = dict[str, Any]
LOG = logging.getLogger("tico.assistant_relay")
EXCERPT_CHARS = 280
# Open markers for an agent in a thread tree, by the partial index messages_awaiting_reply.
_OPEN_SQL = (
    "SELECT id, refs FROM messages WHERE json_extract(refs,'$.awaiting_reply.agent')=? "
    "AND json_extract(refs,'$.awaiting_reply.root')=? AND json_extract(refs,'$.awaiting_reply.reply_id') IS NULL "
    "AND deleted_at IS NULL ORDER BY created_at, rowid LIMIT 50"
)
# What may carry private work besides a task (task_privacy.message_tasks reads those): no excerpt.
PRIVATE_REFS = frozenset({"page", "file", "files", "meeting", "live_meeting", "call", "attachments"})


def _work() -> Any:
    from . import batch_work

    return batch_work


def replied(conn: sqlite3.Connection, reply: Row, conv: Row | None) -> None:
    """An agent wrote `reply` in `conv`: it consumes every marker waiting on that agent in the thread tree, and each
    person waiting is told once, before the write commits (at once on a connection outside a store transaction)."""
    agent = str(reply.get("sender_id") or "")
    slug = str(getattr(getattr(conn, "settings", None), "assistant_bot", "") or "assistant")
    if agent == "bot:" + slug or not conv:
        return
    root = str(conv.get("root_id") or conv["id"])
    try:
        asks = conn.execute(_OPEN_SQL, (agent, root)).fetchall()
    except sqlite3.Error:
        LOG.exception("assistant relay lookup failed for message %s", reply.get("id"))
        return
    if not asks:
        return
    people: list[str] = []
    with _work().isolated(conn, "assistant_relay", reply["id"], private=True):
        for ask in asks:
            conn.execute(
                "UPDATE messages SET refs=json_set(refs,'$.awaiting_reply.reply_id',?) WHERE id=?",
                (reply["id"], ask[0]),
            )
            person = _person(ask[1])
            if person and person not in people:
                people.append(person)
    owed = (str(reply["id"]), root, people)
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
    owed = cast(list[tuple[str, str, list[str]]], pending)
    while owed:
        _deliver(conn, *owed.pop(0))


def _person(raw: object) -> str:
    from . import hubdb

    H: Any = hubdb
    refs: object = H._json(raw, {})
    mark: object = cast(Row, refs).get("awaiting_reply") if isinstance(refs, dict) else None
    return str(cast(Row, mark).get("person") or "") if isinstance(mark, dict) else ""


def _deliver(conn: sqlite3.Connection, reply_id: str, root: str, people: list[str]) -> None:
    from . import hubdb

    H: Any = hubdb
    reply: Row | None = H.message(conn, reply_id)
    if not reply:
        return
    for person in people:
        with _work().isolated(conn, "assistant_relay", f"{reply_id}:{person}", private=True):
            if task_requirements.active_person(conn, person):
                _tell(conn, reply, root, person)


def _own_chat(conn: sqlite3.Connection, root: str, person: str, agent: str) -> bool:
    """Whether the thread tree is the person's own one-to-one chat with the agent: a native DM whose members are just
    the two of them, or a legacy personal room of theirs with only the two."""
    from . import hubdb

    H: Any = hubdb
    home: Row = H.conversation(conn, root) or {}
    if home.get("scope") == "direct":
        members = {str(r[0]) for r in conn.execute("SELECT actor FROM thread_members WHERE thread_id=?", (root,))}
        return members == {person, agent}
    return (
        home.get("scope") == "personal"
        and home.get("owner_id") == person
        and sorted(H._json(home.get("participants"), []) or []) == sorted([person, agent])
    )


def _tell(conn: sqlite3.Connection, reply: Row, root: str, person: str) -> None:
    from . import hubdb, task_privacy, threads

    H: Any = hubdb
    privacy: Any = task_privacy
    chats: Any = threads
    agent = str(reply["sender_id"])
    name = task_effects.plain_line(H.actor_name(conn, agent), "An agent")
    link = f"[Open the reply](#/thread/{reply['conversation_id']}?m={reply['id']})"
    viewer = chats.actor_viewer(conn, person)
    readable = (
        viewer is not None
        and chats.message_readable(conn, viewer, reply)
        and privacy.message_readable(conn, person, reply)
    )
    refs: object = H._json(reply.get("refs"), {})
    plain = (
        isinstance(refs, dict) and not PRIVATE_REFS & set(cast(Row, refs)) and not privacy.message_tasks(conn, reply)
    )
    words = " ".join(str(reply.get("body") or "").split())
    if words and readable and plain and "#/" not in words and _own_chat(conn, root, person, agent):
        cut = words[:EXCERPT_CHARS].rstrip() + ("…" if len(words) > EXCERPT_CHARS else "")
        said = f"{name} replied: {task_effects.plain_line(cut)}"
    else:
        said = f"{name} replied"
    key = f"assistant_relay:{reply['id']}:{person}"
    if conn.execute("SELECT 1 FROM messages WHERE wake_key=?", (key,)).fetchone():
        return  # told already: one relay per reply per person
    line: Row = {"assistant": True, "assistant_relay": True, "quiet": True, "no_mentions": True}
    task_effects.assistant_line(conn, person, f"{said}\n\n{link}", line, key)
