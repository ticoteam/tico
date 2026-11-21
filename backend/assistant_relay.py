# pyright: strict
"""Another agent's answer to what the person's Assistant asked it for them comes back to the person's Assistant chat.

When the Assistant writes to an agent for the person (a delegated send: `hubdb.VIA` is "assistant"), `_write_message`
puts a server-owned marker on that message, `refs.awaiting_reply` {person, agent, root, thread}: no writer can set it.
The first message that agent then writes in that thread, or in any thread under it, consumes every such marker there at
once, in its own transaction (its id goes on each as `reply_id`, whether or not it is ever delivered). Just before that
write commits (`flush`, from store.Store.transaction), each person waiting on it is told once in their Assistant chat:
"<agent> replied", with a link that opens the reply. Only a plain reply in the person's own chat with that agent carries
a short excerpt, decided on the message as it will commit; a reply they cannot read is not told at all. Each delivery is
best-effort in its own savepoint: a failure is logged, never touches the reply and never reopens a marker.
"""

import logging
import sqlite3
from typing import Any, cast

from . import task_effects, task_requirements

Row = dict[str, Any]
LOG = logging.getLogger("tico.assistant_relay")
EXCERPT_CHARS = 280
# Open markers for an agent in a thread tree (the partial index messages_awaiting_reply), asked in the reply's own thread
# or one above it: every one, however many.
_OPEN_SQL = (
    "SELECT id, refs FROM messages WHERE json_extract(refs,'$.awaiting_reply.agent')=? "
    "AND json_extract(refs,'$.awaiting_reply.root')=? AND json_extract(refs,'$.awaiting_reply.reply_id') IS NULL "
    "AND deleted_at IS NULL AND json_extract(refs,'$.awaiting_reply.thread') IN ({threads}) ORDER BY created_at, rowid"
)


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
    # The reply's thread and every thread above it (its path, "/<root>/.../<id>/"): an ask in a sibling is not answered.
    above = [part for part in str(conv.get("path") or "").split("/") if part] or [root, str(conv["id"])]
    try:
        found = conn.execute(_OPEN_SQL.format(threads=",".join("?" * len(above))), (agent, root, *above)).fetchall()
    except sqlite3.Error:
        LOG.exception("assistant relay lookup failed for message %s", reply.get("id"))
        return
    asks = [(str(a[0]), _mark(a[1])) for a in found]
    if not asks:
        return
    people: list[str] = []
    with _work().isolated(conn, "assistant_relay", reply["id"], private=True):
        for ask, mark in asks:
            conn.execute(
                "UPDATE messages SET refs=json_set(refs,'$.awaiting_reply.reply_id',?) WHERE id=?",
                (reply["id"], ask),
            )
            person = str(mark.get("person") or "")
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


def _mark(raw: object) -> Row:
    """The awaiting_reply marker in a stored refs value, or {}."""
    from . import hubdb

    H: Any = hubdb
    refs: object = H._json(raw, {})
    mark: object = cast(Row, refs).get("awaiting_reply") if isinstance(refs, dict) else None
    return cast(Row, mark) if isinstance(mark, dict) else {}


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
    """Whether the thread tree is the person's own one-to-one chat with the agent: a personal thread whose members now
    are just the two of them (threads._dm_members: a native DM's member rows, or a legacy room's owner and agent)."""
    from . import threads

    chats: Any = threads
    home: Row | None = chats.thread_row(conn, root)
    if not home or chats.audience_of(home) != "personal":
        return False
    return set(chats._dm_members(conn, home)) == {person, agent}


def _plain(conn: sqlite3.Connection, reply: Row) -> bool:
    """Whether the reply, as it will commit, names no task, page or meeting, by the parsers the read checks use:
    task_privacy.message_tasks, files.page_references (attachments and body_sources too) and the live meetings'
    provenance (live_privacy)."""
    from . import files, live_privacy, task_privacy

    privacy: Any = task_privacy
    pages: Any = files
    meetings: Any = live_privacy
    content: Row = {"body": reply.get("body"), "refs": reply.get("refs")}
    return not (
        privacy.message_tasks(conn, reply)
        or pages.page_references(content, conn)
        or meetings.sources(conn).references(reply.get("refs"))
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
    if not (
        viewer is not None
        and chats.message_readable(conn, viewer, reply)
        and privacy.message_readable(conn, person, reply)
    ):
        return  # a reply they cannot read is not told at all: hidden and absent look the same
    words = " ".join(str(reply.get("body") or "").split())
    if words and _plain(conn, reply) and _own_chat(conn, root, person, agent):
        cut = words[:EXCERPT_CHARS].rstrip() + ("…" if len(words) > EXCERPT_CHARS else "")
        said = f"{name} replied: {task_effects.plain_line(cut)}"
    else:
        said = f"{name} replied"
    key = f"assistant_relay:{reply['id']}:{person}"
    if conn.execute("SELECT 1 FROM messages WHERE wake_key=?", (key,)).fetchone():
        return  # told already: one relay per reply per person
    line: Row = {"assistant": True, "assistant_relay": True, "quiet": True, "no_mentions": True}
    task_effects.assistant_line(conn, person, f"{said}\n\n{link}", line, key)
