# pyright: strict
"""The company-wide pause: one record that every bot on the team is under until a person resumes it. And a bot's own
pause (`pause_bot`), the same kind of hold on one bot.

- The record is an open `paused` hold on the company (backend/holds.py, holdable_id ''): who paused is its
  created_by_id, when its created_at. Pause opens it when none is open; Resume releases it, kept as history. There is
  never a second record: every surface that reads the pause reads this one.
- Who may pause or resume: the owner and admins (`auth.company_role`), signed in as a person. Tico support sessions,
  bots and service keys are refused, with the reason. A refusal changes nothing.
- A pause or resume that changes nothing (already paused, or nothing to resume) succeeds and says so, and the first
  pauser stays on record.
- Separate from a bot's own pause (an open `paused` hold on the bot, `pause_bot`): resuming the company does not lift a
  bot paused on its own. `read` counts those bots. The bot's hold also sets bots.state 'paused', the switch the rest
  of Tico reads (placement, routines, deliveries, its key): the hold keeps who, why and the history.
- Separate from the billing pause (`company_billing.paused`): this module never reads or writes it.
- While it is on record no new turn starts: the claim refuses every bot (`company_paused`, read through
  backend/holds.py), queued work waits with a held line on its task, and task cards say the company is paused.
"""

import json
import logging
import sqlite3
from collections.abc import Callable
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, TypedDict, cast

from . import holds
from . import hubdb as H
from .store import Problem

if TYPE_CHECKING:
    from .auth import Auth, Identity

PAUSED = holds.PAUSED  # the holds kind: on the company (holdable_id '') and on a bot
CONTROLLERS = ("owner", "admin")
HELD = "company.held"  # the event that marks a held line written on a task in one pause
log = logging.getLogger(__name__)

# The hub's helpers and the auth class are not annotated in full; these name their shapes for this module.
_human = cast("Callable[[sqlite3.Connection, str], dict[str, Any] | None]", H.human)
_actor_id = cast("Callable[[str], str]", H.actor_id)
_task_comment = cast("Callable[..., object]", H.task_comment)
_hub_event = cast("Callable[[sqlite3.Connection, str, str, str, dict[str, Any]], object]", H.event)
_event = cast("Callable[[sqlite3.Connection, str, str, str, dict[str, Any]], object]", H.event)


class Record(TypedDict):
    paused_by: str
    paused_at: str
    paused_by_name: str


class PauseBy(TypedDict):
    name: str
    at: str


class Reading(TypedDict):
    paused: bool
    paused_by: PauseBy | None
    bots_paused_on_own: int


def record(c: sqlite3.Connection) -> Record | None:
    """The one company pause on record, or None when the company runs."""
    # Read through read_cache (holds.get): a status page asks for every bot's line, and the snapshot reads it once.
    found = holds.get(c, PAUSED, "company", "")
    if not found:
        return None
    paused_at = str(found["created_at"])
    paused_by = str(found["created_by_id"])
    person = _human(c, _actor_id(paused_by)) if paused_by.startswith("human:") else None
    name = str((person or {}).get("name") or "") or "a person on Tico Team"
    return {"paused_by": paused_by, "paused_at": paused_at, "paused_by_name": name}


def pause_bot(c: sqlite3.Connection, actor: str, bot: str, reason: str = "") -> None:
    """A bot's own pause: an open `paused` hold on it, the first pauser kept on record. The hold sets bots.state
    'paused' (holds.STATE_KINDS)."""
    if not holds.get(c, PAUSED, "bot", bot):
        holds.put(c, PAUSED, "bot", bot, actor=actor, reason=reason)


def resume_bot(c: sqlite3.Connection, actor: str, bot: str) -> None:
    """End a bot's own pause, kept as history: a person's resume, else BotOps's (or Tico's for it). A bot still
    'paused' with no other stopping hold is active again (holds.STATE_KINDS)."""
    holds.release(c, PAUSED, "bot", bot, actor=actor, how="person" if actor.startswith("human:") else "botops")


def bots_paused_on_own(c: sqlite3.Connection) -> int:
    """How many bots stay paused on their own: their own pause, not this company one."""
    row = cast("tuple[int]", c.execute("SELECT COUNT(*) FROM bots WHERE state='paused'").fetchone())
    return int(row[0])


def read(c: sqlite3.Connection) -> Reading:
    """The company pause for the banner and Settings: whether it is on, who paused, when, and the bots that stay paused
    on their own. The record's shape, for tickets 2 to 6: paused, paused_by {name, at}, bots_paused_on_own."""
    found = record(c)
    paused_by: PauseBy | None = None
    if found is not None:
        paused_by = {"name": found["paused_by_name"], "at": found["paused_at"]}
    return {"paused": found is not None, "paused_by": paused_by, "bots_paused_on_own": bots_paused_on_own(c)}


def may_control(auth: "Auth", who: "Identity") -> bool:
    """Whether this person may pause or resume every bot: the owner and admins (Q2 of the spec). A support session
    is not a member of the team, so it is refused even where it reads as an admin."""
    if who.support or who.role not in ("owner", "human"):
        return False
    role = cast("Callable[[Identity], str]", auth.company_role)
    return role(who) in CONTROLLERS


def pause(c: sqlite3.Connection, auth: "Auth", who: "Identity") -> dict[str, Any]:
    """Pause every bot on the team. Refused unless the caller is the owner or an admin. Answers
    {"changed": bool, "state": read} and writes one audit row only when it sets the pause."""
    if not may_control(auth, who):
        raise Problem("forbidden", "Only the owner or an admin can pause all bots on Tico Team", 403)
    if record(c):
        return {"changed": False, "state": read(c)}
    holds.put(c, PAUSED, "company", "", actor=who.actor)
    _event(c, who.actor, "company.pause", "company", {"bots_paused_on_own": bots_paused_on_own(c)})
    hold_queued(c)
    return {"changed": True, "state": read(c)}


def resume(c: sqlite3.Connection, auth: "Auth", who: "Identity") -> dict[str, Any]:
    """Lift the company pause. Refused unless the caller is the owner or an admin. Bots paused on their own stay paused.
    Answers {"changed": bool, "state": read}; with nothing paused it changes nothing and says so."""
    if not may_control(auth, who):
        raise Problem("forbidden", "Only the owner or an admin can resume all bots on Tico Team", 403)
    if not record(c):
        return {"changed": False, "state": read(c)}
    held = queued_jobs(c)
    holds.release(c, PAUSED, "company", "", actor=who.actor, how="person")
    _event(c, who.actor, "company.resume", "company", {"bots_paused_on_own": bots_paused_on_own(c), "held": held})
    return {"changed": True, "state": read(c), "held": held}


def _when(value: str) -> str:
    """`2026-11-08T12:38:00.000000Z` as "12:38 UTC, Nov 8" for the words a person reads."""
    try:
        when = datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)
    except ValueError:
        return value
    return f"{when:%H:%M} UTC, {when:%b} {when.day}"


def waiting_words(found: Record) -> str:
    """What a queued task shows while the company is paused (the page reads it beside the task's column)."""
    return f"all bots are paused by {found['paused_by_name']}. Resume to start."


def _held_line(found: Record) -> str:
    return (
        f"Held: all bots on Tico Team are paused by {found['paused_by_name']} since {_when(found['paused_at'])}. "
        "The owner or an admin can resume them; this task starts when they do."
    )


def queued_task_ids(c: sqlite3.Connection, bot: str) -> list[str]:
    """The tasks with queued work for this bot, in queue order, each once."""
    rows = c.execute(
        "SELECT json_extract(m.refs,'$.task') FROM jobs j JOIN messages m ON m.id=j.message_id "
        "WHERE j.bot_id=? AND j.state='queued' AND json_extract(m.refs,'$.task') IS NOT NULL "
        "ORDER BY j.created_at, j.rowid",
        (bot,),
    ).fetchall()
    found: list[str] = []
    for row in rows:
        task_id = str(row[0])
        if task_id not in found:
            found.append(task_id)
    return found


def hold_task(c: sqlite3.Connection, task_id: str, found: Record) -> bool:
    """Write the held line on a task once per pause (S3): a Hub comment that wakes no bot, and an event that marks it.
    True when the line was written now."""
    marked = c.execute(
        "SELECT 1 FROM events WHERE action=? AND target=? AND json_extract(detail,'$.paused_at')=? LIMIT 1",
        (HELD, task_id, found["paused_at"]),
    ).fetchone()
    if marked:
        return False
    try:
        _task_comment(
            c, H.KEEPER, task_id, _held_line(found), wake=False, extra_refs={"company_held": found["paused_at"]}
        )
    except H.Refused:
        log.info("The held line for task %s was refused; the pause still holds its work", task_id)
        return False
    _hub_event(c, H.KEEPER, HELD, task_id, {"paused_at": found["paused_at"]})
    return True


def hold_queued(c: sqlite3.Connection) -> int:
    """Write the held lines for every task with queued work for a bot while the company is paused. Returns how many."""
    found = record(c)
    if not found:
        return 0
    bots = [str(row[0]) for row in c.execute("SELECT DISTINCT bot_id FROM jobs WHERE state='queued'")]
    return sum(hold_task(c, task_id, found) for bot in bots for task_id in queued_task_ids(c, bot))


def hold_new_job(c: sqlite3.Connection, message: dict[str, Any]) -> bool:
    """A job queued to a bot while the company is paused: the held line on its task, once per pause. A held line's own
    message carries company_held in its refs, so it is not held again."""
    found = record(c)
    refs: Any = message.get("refs")
    if isinstance(refs, str):
        refs = json.loads(refs) if refs.startswith("{") else None
    if not found or not isinstance(refs, dict):
        return False
    fields = cast("dict[str, Any]", refs)
    task_id = fields.get("task")
    if not isinstance(task_id, str) or not task_id or fields.get("company_held"):
        return False
    return hold_task(c, task_id, found)


def queued_jobs(c: sqlite3.Connection) -> int:
    """How many jobs are queued to any bot: the work the pause holds, which starts in queue order once it is lifted."""
    row = cast("tuple[int]", c.execute("SELECT COUNT(*) FROM jobs WHERE state='queued'").fetchone())
    return int(row[0])
