# pyright: strict
"""A card's slot: one run at a time per (visit, agent), piloted on the pipelines in SWITCHED (master plan P2, S2).

Every message about a card still queues its own job (backend/enqueue.py), so a run's message stays what it was. A job
for a message in the card's own room (a thread's carrier counts as the message it carries), in a switched pipeline,
also carries its slot, `<visit id>:<bot id>` (`assign`):

- While one of a slot's jobs has a run, its other queued jobs wait (`busy`), then run one at a time by the usual pick.
- A slot's run carries its own job alone: nothing is steered into it and its completion coalesces nothing, and no
  slot job is steered into or coalesced by another run (`Execution.inputs`, `complete`). So a completion settles only
  its own job, never the work of the next visit or of the next agent, which is another slot's.
- The card's room is the baseline audience: a message whose provenance narrows it further keeps its own older run.
- While any agent has live or unsettled work in the card's room from before the switch, the card stays on the older
  path, so one scheduler drives a card at a time.

SWITCHED is temporary: it goes, with the older path, once every pipeline runs by slot.
"""

import json
from typing import Any, cast

from . import hubdb

H: Any = hubdb
Row = dict[str, Any]

SWITCHED = frozenset({"video production"})  # pipeline names, case-folded
LIVE = ("queued", "leased", "running", "input", "uncertain")  # older work not settled yet
RUNNING = ("leased", "running", "input")
_SWITCHED_SQL = f"SELECT 1 FROM pipelines WHERE name IN ({','.join('?' * len(SWITCHED))}) AND retired_at IS NULL LIMIT 1"  # pipelines.name is NOCASE


def _refs(value: object) -> Row:
    if isinstance(value, (str, bytes)):
        value = json.loads(value or "{}")
    return cast(Row, value) if isinstance(value, dict) else {}


def of(row: Any) -> str | None:
    """The slot of a job row (a dict or a database row), or None."""
    return row["slot"] if row is not None and "slot" in row.keys() else None


def busy(conn: Any, row: Any) -> bool:
    """Whether another job of `row`'s slot has a run now: `row` waits for it."""
    slot = of(row)
    if not slot:
        return False
    marks = ",".join("?" * len(RUNNING))
    found = conn.execute(
        f"SELECT 1 FROM jobs WHERE slot=? AND state IN ({marks}) AND id<>? LIMIT 1", (slot, *RUNNING, row["id"])
    ).fetchone()
    return found is not None


def _narrows(conn: Any, message: Row, private_room: bool) -> bool:
    """Whether the message's provenance narrows the card room's audience. In a public room that is any reader of the
    room who may not read it (threads.message_narrower). A private card's room is narrow already, so only a message
    kept from some of its own readers (a legacy audience, on it or on the message it carries) narrows it."""
    from . import threads

    T: Any = threads  # untyped
    if not private_room:
        return bool(T.message_narrower(conn, message))
    found = conn.execute(
        "SELECT m.legacy_audience, s.legacy_audience FROM messages m "
        "LEFT JOIN messages s ON s.id = json_extract(m.refs,'$.carries') WHERE m.id=?",
        (message["id"],),
    ).fetchone()
    return bool(found and (found[0] or found[1]))


def assign(conn: Any, message: Row) -> str | None:
    """Give the job `message` just queued its slot, when the message is about a card in a switched pipeline and sits in
    the card's own room: a message elsewhere (a private chat that names the card) has another audience and keeps its
    own run. A thread's carrier is its card's when the message it carries is (`task_wakes.message_task`); a thread's
    private-notes wake never is. Returns the slot, or None for a job that stays on the older path."""
    from . import task_wakes, threads

    T: Any = threads  # untyped
    refs = _refs(message.get("refs"))
    if refs.get("command") or refs.get("live_meeting") or refs.get("wake") == T.NOTE_WAKE:
        return None  # a server command, a meeting's turn or a thread's notes are not the card's work
    if not H._has_table(conn, "visits"):  # a bare hub database has no cards
        return None
    # Two key reads before anything else: most messages are not in a card's room, and most companies switch nothing.
    room = conn.execute(
        "SELECT cv.id, cv.task_id FROM messages m JOIN conversations cv ON cv.id = m.conversation_id WHERE m.id=?",
        (message["id"],),
    ).fetchone()
    if not room or not room[1] or not conn.execute(_SWITCHED_SQL, tuple(SWITCHED)).fetchone():
        return None
    task_id = task_wakes.message_task(conn, message["id"])
    found = conn.execute(
        "SELECT v.id, p.name, t.private FROM tasks t JOIN visits v ON v.task_id = t.id AND v.left_at IS NULL "
        "JOIN stages s ON s.id = v.stage_id JOIN pipelines p ON p.id = s.pipeline_id WHERE t.id=? AND t.conversation_id=?",
        (task_id or "", room[0]),
    ).fetchone()
    if not found or str(found[1]).casefold() not in SWITCHED:
        return None
    if _narrows(conn, message, found[2] is None or bool(found[2])):
        return None  # some reader of the card's room may not read it: its own run answers it
    # Any agent's live work in the card's room from before the switch settles first: until then the card stays on
    # the older path, a reassign mid-run included. Work that never gets a slot (above, or kept from some of the room)
    # does not count.
    legacy = conn.execute(
        "SELECT 1 FROM jobs j JOIN messages m ON m.id = j.message_id WHERE j.slot IS NULL AND j.id<>? "
        f"AND j.state IN ({','.join('?' * len(LIVE))}) AND m.conversation_id=? "
        "AND coalesce(json_extract(m.refs,'$.command'),0)=0 AND json_extract(m.refs,'$.live_meeting') IS NULL "
        "AND coalesce(json_extract(m.refs,'$.wake'),'')<>? AND m.legacy_audience IS NULL LIMIT 1",
        (message["id"], *LIVE, room[0], T.NOTE_WAKE),
    ).fetchone()
    if legacy:
        return None
    slot = f"{found[0]}:{str(message['recipient_id'])[4:]}"
    conn.execute("UPDATE jobs SET slot=? WHERE id=?", (slot, message["id"]))
    return slot
