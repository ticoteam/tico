# pyright: strict
"""A card's slot: one run at a time per (visit, agent), piloted on the pipelines in SWITCHED (master plan P2, S2).

Every message about a card still queues its own job (backend/enqueue.py), so a run's message stays what it was. A job
about a card in a switched pipeline also carries its slot, `<visit id>:<bot id>` (`assign`):

- Its claim hands the run the slot's stage wake as its message when one is queued (`carrier`), so the stage's work is
  the run and never news inside a chat run, and every other queued job of the slot as input given in its prompt
  (`Execution.fold_behind`). `jobs.slot_rev` records the input revision handed over: the newest message (rowid).
- Nothing is steered into a slot's run, and its completion coalesces nothing (`Execution.inputs`, `complete`). What
  arrives during the run stays queued and starts the slot's next run once this one ends. So a completion settles only
  what its claim handed it, up to `slot_rev`, and a run that ends after a reassignment, reopen or stage move settles no
  successor work: that work is another slot's.
- A card with a live job from before its pipeline switched stays on the older path until that job settles, so one
  scheduler drives a card at a time.

SWITCHED is temporary: it goes, with the older path, once every pipeline runs by slot.
"""

import json
from typing import Any, cast

from . import hubdb

H: Any = hubdb
Row = dict[str, Any]

SWITCHED = frozenset({"video production"})  # pipeline names, case-folded
LIVE = ("queued", "leased", "running", "input")


def _refs(value: object) -> Row:
    if isinstance(value, (str, bytes)):
        value = json.loads(value or "{}")
    return cast(Row, value) if isinstance(value, dict) else {}


def assign(conn: Any, message: Row) -> str | None:
    """Give the job `message` just queued its slot, when the message is about a card in a switched pipeline. Returns
    the slot, or None for a job that stays on the older path."""
    refs = _refs(message.get("refs"))
    if refs.get("command") or refs.get("live_meeting") or refs.get("carries"):
        return None  # a server command, a meeting's turn or a thread's carrier is not the card's work
    task_id = H.refs_task_id(refs)
    if not task_id and message.get("conversation_id"):
        room = conn.execute("SELECT task_id FROM conversations WHERE id=?", (message["conversation_id"],)).fetchone()
        task_id = H.message_task_id({}, {"task_id": room[0]}) if room else None
    if not task_id or not H._has_table(conn, "visits"):  # a bare hub database has no cards
        return None
    found = conn.execute(
        "SELECT v.id, p.name FROM visits v JOIN stages s ON s.id = v.stage_id JOIN pipelines p ON p.id = s.pipeline_id "
        "WHERE v.task_id=? AND v.left_at IS NULL",
        (task_id,),
    ).fetchone()
    if not found or str(found[1]).casefold() not in SWITCHED:
        return None
    bot = str(message["recipient_id"])[4:]
    legacy = conn.execute(
        "SELECT 1 FROM jobs j JOIN messages m ON m.id = j.message_id JOIN conversations cv ON cv.id = m.conversation_id "
        f"WHERE j.bot_id=? AND j.id<>? AND j.slot IS NULL AND j.state IN ({','.join('?' * len(LIVE))}) "
        f"AND {H.MESSAGE_TASK_SQL}=? LIMIT 1",
        (bot, message["id"], *LIVE, task_id),
    ).fetchone()
    if legacy:
        return None  # its work from before the switch settles first
    slot = f"{found[0]}:{bot}"
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
