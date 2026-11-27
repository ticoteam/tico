# pyright: strict
"""Task rooms: a bot's task has a room of its own (kind "task"), so its traffic stays out of the chat its main
session reads.

The main chat gets a quiet Tico line about it, `refs.task_started`, when the task is filed from that chat. It is
written with `quiet`, so it queues no run; the main session reads it in its history the next time something real wakes
it (execution.lane_history).

Split out of hubdb.py, which re-exports every name here, so `H.give_task_room` and `from .hubdb import ...` callers
are unchanged. This module imports nothing from hubdb at load, so hubdb can import it; it reaches hubdb's helpers
through `_H()` when it runs.
"""

from typing import Any

from . import hubdb_core
from .hubdb_core import TICO_ACTOR

_core: Any = hubdb_core  # its actor helpers are untyped: bound as Any, as strict modules read them
actor_id = _core.actor_id
is_bot = _core.is_bot
is_human = _core.is_human


# What hubdb re-exports: the private ones too, which hubdb itself and its callers use.
__all__ = [
    "_room_person",
    "_room_reads",
    "_task_started",
    "task_from_chat",
    "_task_room",
    "give_task_room",
]


def _H() -> Any:
    from . import hubdb

    return hubdb


def _room_person(conn: Any, room: Any, row: Any) -> Any:
    """The person a main-chat line about `row` is addressed to: the room's owner, else a person in it."""
    people = [p for p in room.get("participants") or () if is_human(p)]
    if room.get("owner_id"):
        return room["owner_id"]
    return row["requester_id"] if row.get("requester_id") in people else people[0] if people else row["owner_id"]


def _room_reads(conn: Any, room: Any, row: Any):
    """Whether everyone in the room may read the task, so a line naming it says nothing new to anyone."""
    H = _H()
    readers = set(room.get("participants") or []) | {room.get("owner_id")} - {None}
    return all(H.task_private_readable(conn, who, row) for who in readers)


def _task_started(conn: Any, row: Any, room: Any):
    """The quiet "Started task" line in the chat a bot's task was filed from. A private task is named
    only in a room whose every reader may read it."""
    H = _H()
    if not room or room["id"] == row["conversation_id"]:
        return None
    if H.task_private(conn, row) and not _room_reads(conn, room, row):
        return None
    msg = H._write_message(
        conn,
        TICO_ACTOR,
        _room_person(conn, room, row),
        f"Started task: {row['title']}",
        room,
        "notice",
        {"task": row["id"], "task_started": True, "quiet": True},
        None,
        None,
    )
    H.event(conn, TICO_ACTOR, "task.started_in", row["id"], {"conversation": room["id"], "message": msg["id"]})
    return msg


def task_from_chat(conn: Any, task_id: Any):
    """Whether a bot's task came from a chat (filed there, or moved out of one): its turns' replies reach
    the person in its room as they would have in the chat, not only once it is done (execution.complete)."""
    H = _H()
    return bool(
        H._one(
            conn,
            "SELECT 1 FROM events WHERE actor=? AND action IN ('task.started_in','task.room') AND target=? LIMIT 1",
            (TICO_ACTOR, task_id),
        )
    )


def _task_room(conn: Any, row: Any, parties: Any):
    """A new room of row's own (kind "task"), with `parties` in it; the task points at it."""
    H = _H()
    room = H.new_id()
    conn.execute(
        "INSERT INTO conversations(id,kind,subject,participants,created_at,scope,task_id) VALUES(?,?,?,?,?,?,?)",
        (room, "task", row["title"], H._dump(parties), H.now(), "task", row["id"]),
    )
    conn.execute("UPDATE tasks SET conversation_id=? WHERE id=?", (room, row["id"]))
    return H.conversation(conn, room)


def give_task_room(conn: Any, row: Any):
    """A bot's live task that still lives in a chat room (filed before task rooms) moves to a room of its
    own, as execution.claim hands out a turn on it. Its earlier messages stay in the chat; the new room
    opens with a line pointing there. Returns the task as it now is."""
    H = _H()
    if not row or not is_bot(row["owner_id"]) or row["status"] not in H.ACTIVE_STATUSES:
        return row
    old = H.conversation(conn, row["conversation_id"])
    if not old or old.get("kind") != "chat":
        return row
    person = _room_person(conn, old, row) if not H.task_private(conn, row) else None
    room = _task_room(conn, row, list(dict.fromkeys(p for p in (row["requester_id"], row["owner_id"], person) if p)))
    owner: dict[str, Any] = H.bot(conn, row["owner_id"]) or {}
    name = owner.get("display_name") or actor_id(row["owner_id"])
    H._write_message(
        conn,
        TICO_ACTOR,
        row["owner_id"],
        f"Earlier messages are in {name} chat.",
        room,
        "notice",
        {"task": row["id"], "quiet": True, "moved_from": old["id"]},
        None,
        None,
    )
    H.event(conn, TICO_ACTOR, "task.room", row["id"], {"from": old["id"], "to": room["id"]})
    return H.task(conn, row["id"])
