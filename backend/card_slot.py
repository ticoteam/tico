# pyright: strict
"""A card's slot: one run at a time per (visit, agent), piloted on the pipelines in SWITCHED (master plan P2, S2).

Every message about a card still queues its own job (backend/enqueue.py), so a run's message stays what it was. A job
for a message in the card's own room (a thread's carrier counts as the message it carries), in a switched pipeline,
also carries its slot, `<visit id>:<bot id>` (`assign`):

- The claim picks its job by the usual rules. Its run then takes the slot's other queued jobs of the same room and
  audience in its prompt, up to FOLD_MAX jobs and FOLD_BYTES (`Execution.fold_behind`), unless its job carries nothing
  else (`carries_more`: a people-only claim, a redirect, a claim-error probe). `jobs.slot_rev` records the newest message
  handed over; the slot's work not handed stays out of the run's history (`pending`). A message whose provenance some
  reader of the room may not read gets no slot.
- Nothing is steered into a slot's run, and its completion coalesces nothing (`Execution.inputs`, `complete`). What
  arrives during the run stays queued and starts the slot's next run. So a completion settles only what its claim handed
  it, and a run that ends after a reassignment, reopen or stage move settles no successor work: that work is another
  slot's. A run that never starts (stopped, refused, handed back) puts the jobs it took back in the queue (`release`).
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
LIVE = ("queued", "leased", "running", "input", "uncertain")  # older work not settled yet
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

    T: Any = threads  # untyped

    refs = _refs(message.get("refs"))
    if refs.get("command") or refs.get("live_meeting") or refs.get("wake") == T.NOTE_WAKE:
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
    if T.message_narrower(conn, message):
        return None  # some reader of the room may not read it (or what it carries): its own run answers it
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


def carries_more(conn: Any, row: Any, people_only: bool) -> bool:
    """Whether the run of the claimed job `row` takes the slot's other queued jobs: not on a computer that starts only
    people's messages, not for a redirect (it replaces a stopped turn) and not for a probe after a claim error."""
    if people_only or row["redirect"]:
        return False
    probe = conn.execute(
        "SELECT 1 FROM holds WHERE kind='claim_error' AND holdable_type='job' AND holdable_id=? AND released_at IS NULL",
        (row["id"],),
    ).fetchone()
    return probe is None


def release(conn: Any, attempt_id: str, job_id: str) -> None:
    """A turn that never started gives back the slot jobs folded into it: they wait for the slot's next run, and what
    ends the turn (a stop, a refusal, a failed start) is its own job's alone."""
    conn.execute(
        "UPDATE jobs SET state='queued', attempt_id=NULL WHERE attempt_id=? AND id<>? AND slot IS NOT NULL "
        "AND state='input'",
        (attempt_id, job_id),
    )


def handed(conn: Any, job_id: str, message_ids: list[str]) -> None:
    """Record on the claimed job the input revision its run was handed: the newest of these messages."""
    marks = ",".join("?" * len(message_ids))
    conn.execute(
        f"UPDATE jobs SET slot_rev=(SELECT max(rowid) FROM messages WHERE id IN ({marks})) WHERE id=?",
        (*message_ids, job_id),
    )


def pending(conn: Any, slot: str, message_ids: Collection[str]) -> set[str]:
    """Of these messages, the ones whose job still waits in `slot`: work a run was not handed is not in its history."""
    if not message_ids:
        return set()
    marks = ",".join("?" * len(message_ids))
    return {
        str(found[0])
        for found in conn.execute(
            f"SELECT message_id FROM jobs WHERE slot=? AND state='queued' AND message_id IN ({marks})",
            (slot, *message_ids),
        )
    }
