# pyright: strict
"""Another agent's answer to what the person's Assistant asked it for them comes back to the person's Assistant chat.

When the Assistant writes to an agent for the person (a delegated send: `hubdb.VIA` is "assistant"), `_write_message`
puts a server-owned marker on that message, `refs.awaiting_reply` {person, agent, root}: no writer can set it. The first
message that agent then writes in that thread, or in any of its sub-threads, consumes the marker (its id goes on the
marker as `reply_id`, as it is written, whether or not it is ever delivered) and is relayed into the person's Assistant
chat: "<agent> replied", with a link that opens the reply. Only a plain reply in the person's own chat with that agent,
which they still read, carries a short excerpt. Best-effort: a relay that fails is logged and never touches the reply.
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
    "AND deleted_at IS NULL ORDER BY created_at, rowid LIMIT 20"
)
# What may name private work: a reply carrying any of these is relayed without an excerpt.
PRIVATE_REFS = frozenset({"task", "page", "file", "files", "meeting", "live_meeting", "call", "attachments"})


def replied(conn: sqlite3.Connection, reply: Row) -> None:
    """An agent wrote `reply`: relay it for each person whose Assistant is waiting on this agent in this thread."""
    agent, thread = str(reply.get("sender_id") or ""), reply.get("conversation_id")
    slug = str(getattr(getattr(conn, "settings", None), "assistant_bot", "") or "assistant")
    if agent == "bot:" + slug or not thread:
        return
    try:
        found = conn.execute("SELECT coalesce(root_id, id) FROM conversations WHERE id=?", (thread,)).fetchone()
        asks = conn.execute(_OPEN_SQL, (agent, found[0])).fetchall() if found else []
    except sqlite3.Error:
        LOG.exception("assistant relay lookup failed for message %s", reply.get("id"))
        return
    if not asks:
        return
    from . import batch_work

    work: Any = batch_work
    with work.isolated(conn, "assistant_relay", reply["id"], private=True):
        _relay(conn, reply, str(found[0]), [str(a["id"]) for a in asks], [str(a["refs"] or "{}") for a in asks])


def _relay(conn: sqlite3.Connection, reply: Row, root: str, asks: list[str], marks: list[str]) -> None:
    from . import hubdb

    H: Any = hubdb
    people: list[str] = []
    for ask, raw in zip(asks, marks, strict=True):
        conn.execute(
            "UPDATE messages SET refs=json_set(refs,'$.awaiting_reply.reply_id',?) WHERE id=?", (reply["id"], ask)
        )
        refs: object = H._json(raw, {})
        mark: object = cast(Row, refs).get("awaiting_reply") if isinstance(refs, dict) else None
        person = str(cast(Row, mark).get("person") or "") if isinstance(mark, dict) else ""
        if person and person not in people:
            people.append(person)
    for person in people:
        if task_requirements.active_person(conn, person):
            _tell(conn, reply, root, person)


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
    home: Row = H.conversation(conn, root) or {}
    own_chat = (
        home.get("scope") == "personal"
        and home.get("owner_id") == person
        and sorted(H._json(home.get("participants"), []) or []) == sorted([person, agent])
    )
    refs: object = H._json(reply.get("refs"), {})
    plain = isinstance(refs, dict) and not PRIVATE_REFS & set(cast(Row, refs))
    words = " ".join(str(reply.get("body") or "").split())
    if readable and own_chat and plain and words and "#/" not in words:
        cut = words[:EXCERPT_CHARS].rstrip() + ("…" if len(words) > EXCERPT_CHARS else "")
        said = f"{name} replied: {task_effects.plain_line(cut)}"
    else:
        said = f"{name} replied"
    line: Row = {"assistant": True, "assistant_relay": True, "quiet": True, "no_mentions": True}
    task_effects.assistant_line(conn, person, f"{said}\n\n{link}", line, f"assistant_relay:{reply['id']}:{person}")
