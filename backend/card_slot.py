# pyright: strict
"""A card's slot: one run at a time per (visit, agent), piloted on the pipelines in SWITCHED (master plan P2, S2).

Every message about a card still queues its own job (backend/enqueue.py), so a run's message stays what it was. A job
for a message in the card's own room (a thread's carrier counts as the message it carries), in a switched pipeline,
also carries its slot, `<visit id>:<bot id>` (`assign`):

- While one of a slot's jobs has a run, its other queued jobs wait (`busy`), then run one at a time by the usual pick.
- A slot's run carries its own job alone: nothing is steered into it and its completion coalesces nothing, and no
  slot job is steered into or coalesced by another run (`Execution.inputs`, `complete`). So a completion settles only
  its own job, never the work of the next visit or of the next agent, which is another slot's. The bot's own work
  still waiting is not read in a slot run's history either (`pending`), and a fired deadline run keeps its deferred
  items until it is picked (backend/delivery.py `carried`).
- The card's room is the baseline audience: a message whose provenance narrows it further keeps its own older run.
- While any agent has live or unsettled work in the card's room from before the switch, the card stays on the older
  path, so one scheduler drives a card at a time.

SWITCHED is temporary: it goes, with the older path, once every pipeline runs by slot.
"""

import json
from collections.abc import Collection
from typing import Any, cast

from . import hubdb

H: Any = hubdb
Row = dict[str, Any]

SWITCHED = frozenset({"video production"})  # pipeline names, case-folded
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


def _never(refs: Row) -> bool:
    """A server command, a meeting's turn and a thread's notes wake are never a card's work: they get no slot."""
    from . import threads

    T: Any = threads  # untyped
    return bool(refs.get("command") or refs.get("live_meeting") or refs.get("wake") == T.NOTE_WAKE)


def _narrows(conn: Any, message_id: str, private_room: bool) -> bool:
    """Whether a message, or the message it carries, is kept from some reader of the card's room: the card's own access
    is the baseline. One with a legacy audience is; in a public room, so is one some reader of the room may not read
    (threads.message_narrower). A private card's room is narrow already, so only a legacy audience narrows it."""
    from . import threads

    T: Any = threads  # untyped
    found = conn.execute(
        "SELECT m.legacy_audience, s.id, s.legacy_audience FROM messages m "
        "LEFT JOIN messages s ON s.id = json_extract(m.refs,'$.carries') WHERE m.id=?",
        (message_id,),
    ).fetchone()
    if not found or found[0] or found[2]:
        return bool(found)
    if private_room:
        return False
    return bool(
        T.message_narrower(conn, {"id": message_id}) or (found[1] and T.message_narrower(conn, {"id": found[1]}))
    )


# The older path's unsettled jobs in one room, read from the live and uncertain job indexes (jobs_live_created,
# jobs_uncertain), each joined to its message by key: never the room's message history.
_OLDER = (
    "SELECT j.message_id, m.refs FROM jobs j JOIN messages m ON m.id = j.message_id "
    "WHERE j.state IN ('queued','leased','running','input') AND j.slot IS NULL AND j.id<>? AND m.conversation_id=? "
    "UNION ALL SELECT j.message_id, m.refs FROM jobs j JOIN messages m ON m.id = j.message_id "
    "WHERE j.state='uncertain' AND j.slot IS NULL AND j.id<>? AND m.conversation_id=?"
)


def assign(conn: Any, message: Row) -> str | None:
    """Give the job `message` just queued its slot, when the message is about a card in a switched pipeline and sits in
    the card's own room: a message elsewhere (a private chat that names the card) has another audience and keeps its
    own run. A thread's carrier is its card's when the message it carries is (`task_wakes.message_task`); a thread's
    private-notes wake never is. Returns the slot, or None for a job that stays on the older path."""
    from . import task_wakes

    if _never(_refs(message.get("refs"))):
        return None
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
    private = found[2] is None or bool(found[2])
    if _narrows(conn, message["id"], private):
        return None  # some reader of the card's room may not read it: its own run answers it
    # Any agent's unsettled work in the card's room from before the switch settles first: until then the card stays on
    # the older path, a reassign mid-run included. Work that could never have a slot (above) does not count.
    for older, raw in conn.execute(_OLDER, (message["id"], room[0], message["id"], room[0])).fetchall():
        if not _never(_refs(raw)) and not _narrows(conn, older, private):
            return None
    slot = f"{found[0]}:{str(message['recipient_id'])[4:]}"
    conn.execute("UPDATE jobs SET slot=? WHERE id=?", (slot, message["id"]))
    return slot


def pending(conn: Any, bot: str, message_ids: Collection[str]) -> set[str]:
    """Of these messages, the ones `bot` still has a queued job for, or whose carrier it does: its own work waiting for
    a run of its own, in any visit, is not read in another run's history."""
    if not message_ids:
        return set()
    marks = ",".join("?" * len(message_ids))
    found = conn.execute(
        "SELECT j.message_id, json_extract(m.refs,'$.carries') FROM jobs j INDEXED BY jobs_queue "
        "JOIN messages m ON m.id = j.message_id "
        f"WHERE j.bot_id=? AND j.state='queued' AND (j.message_id IN ({marks}) "
        f"OR json_extract(m.refs,'$.carries') IN ({marks}))",
        (bot, *message_ids, *message_ids),
    ).fetchall()
    return {str(value) for row in found for value in row if value}
