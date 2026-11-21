# pyright: strict
"""A card's slot: one run at a time per (visit, agent), piloted on the pipelines in SWITCHED (master plan P2, S2).

Every message about a card still queues its own job (backend/enqueue.py), so a run's message stays what it was. A job
for a message in the card's own room (a thread's carrier counts as the message it carries), in a switched pipeline,
also carries its slot, `<visit id>:<bot id>` (`assign`):

- The claim picks the slot's stage wake as the job it starts when one is queued (`carrier`), before the claim's checks,
  so the stage's work is the run and never news inside a chat run. The run takes the slot's other queued jobs of the
  same room and audience in its prompt, up to FOLD_MAX jobs and FOLD_BYTES (`Execution.fold_behind`); `jobs.slot_rev`
  records the newest message handed over. A computer that starts only people's messages runs the person's alone, and
  its history leaves out the stage work waiting (`pending_wakes`).
- Nothing is steered into a slot's run, and its completion coalesces nothing (`Execution.inputs`, `complete`). What
  arrives during the run stays queued and starts the slot's next run. So a completion settles only what its claim handed
  it, and a run that ends after a reassignment, reopen or stage move settles no successor work: that work is another
  slot's. A run refused before it starts puts the jobs it took back in the queue.
- While any agent has live work in the card's room from before the switch, the card stays on the older path, so one
  scheduler drives a card at a time.

SWITCHED is temporary: it goes, with the older path, once every pipeline runs by slot.
"""

import json
from collections.abc import Collection
from typing import Any, cast

from . import hubdb

H: Any = hubdb
Row = dict[str, Any]

SWITCHED = frozenset({"video production"})  # pipeline names, case-folded
LIVE = ("queued", "leased", "running", "input")
FOLD_MAX = 20  # jobs one run takes in its prompt; the rest wait for the slot's next run
FOLD_BYTES = 32_000  # and their words, in bytes, at most


def _refs(value: object) -> Row:
    if isinstance(value, (str, bytes)):
        value = json.loads(value or "{}")
    return cast(Row, value) if isinstance(value, dict) else {}


def assign(conn: Any, message: Row) -> str | None:
    """Give the job `message` just queued its slot, when the message is about a card in a switched pipeline and sits in
    the card's own room: a message elsewhere (a private chat that names the card) has another audience and keeps its
    own run. A thread's carrier is its card's when the message it carries is (`task_wakes.message_task`); a thread's
    private-notes wake never is. Returns the slot, or None for a job that stays on the older path."""
    from . import task_wakes, threads

    refs = _refs(message.get("refs"))
    if refs.get("command") or refs.get("live_meeting") or refs.get("wake") == threads.NOTE_WAKE:
        return None  # a server command, a meeting's turn or a thread's notes are not the card's work
    if not H._has_table(conn, "visits"):  # a bare hub database has no cards
        return None
    task_id = task_wakes.message_task(conn, message["id"])
    found = conn.execute(
        "SELECT v.id, p.name, t.conversation_id FROM messages m JOIN tasks t ON t.id=? AND t.conversation_id = "
        "m.conversation_id JOIN visits v ON v.task_id = t.id AND v.left_at IS NULL JOIN stages s ON s.id = v.stage_id "
        "JOIN pipelines p ON p.id = s.pipeline_id WHERE m.id=?",
        (task_id or "", message["id"]),
    ).fetchone()
    if not found or str(found[1]).casefold() not in SWITCHED:
        return None
    # Any agent's live work in the card's room from before the switch settles first: until then the card stays on
    # the older path, a reassign mid-run included.
    legacy = conn.execute(
        "SELECT 1 FROM jobs j JOIN messages m ON m.id = j.message_id WHERE j.slot IS NULL AND j.id<>? "
        f"AND j.state IN ({','.join('?' * len(LIVE))}) AND m.conversation_id=? LIMIT 1",
        (message["id"], *LIVE, found[2]),
    ).fetchone()
    if legacy:
        return None
    slot = f"{found[0]}:{str(message['recipient_id'])[4:]}"
    conn.execute("UPDATE jobs SET slot=? WHERE id=?", (slot, message["id"]))
    return slot


def carrier(conn: Any, row: Any, people_only: bool = False) -> Row:
    """The job a claim of `row` starts, as a dict: the slot's oldest queued stage wake when `row` is another job of a
    slot, else `row`. The claim's other columns (the computer's generation) ride along. `people_only`: the computer
    holds every start but a person's own message, so `row`, that message, starts alone."""
    row = {"slot": None, **dict(row)}
    slot = row["slot"]
    if not slot or people_only:
        return row
    found = conn.execute(
        "SELECT j.* FROM jobs j JOIN messages m ON m.id = j.message_id WHERE j.slot=? AND j.state='queued' "
        "AND j.held=0 AND j.redirect=0 AND json_extract(m.refs,'$.wake')='task_turn' "
        "AND coalesce(json_extract(m.refs,'$.quiet'),0)=0 ORDER BY m.rowid LIMIT 1",
        (slot,),
    ).fetchone()
    return {**row, **dict(found)} if found else row


def handed(conn: Any, job_id: str, message_ids: list[str]) -> None:
    """Record on the claimed job the input revision its run was handed: the newest of these messages."""
    marks = ",".join("?" * len(message_ids))
    conn.execute(
        f"UPDATE jobs SET slot_rev=(SELECT max(rowid) FROM messages WHERE id IN ({marks})) WHERE id=?",
        (*message_ids, job_id),
    )


def pending_wakes(conn: Any, message_ids: Collection[str]) -> set[str]:
    """Of these messages, the stage wakes still queued for a run: a run that may not take them (a computer that starts
    only people's messages) does not read them in its history either."""
    if not message_ids:
        return set()
    marks = ",".join("?" * len(message_ids))
    return {
        str(found[0])
        for found in conn.execute(
            f"SELECT m.id FROM messages m JOIN jobs j ON j.message_id = m.id WHERE m.id IN ({marks}) "
            "AND j.state='queued' AND json_extract(m.refs,'$.wake')='task_turn'",
            tuple(message_ids),
        )
    }
