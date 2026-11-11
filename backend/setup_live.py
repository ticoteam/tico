# pyright: strict
"""A seat bot's setup ends with the bot live (company_import._go_live): BotOps builds it on its "Set up" task and takes it
live, and the task is done only once the bot is active and takes work. Saying done earlier would leave the bot `planned`
with nobody left to finish it, while its pipeline's tasks wait on it.

`GOES_LIVE` is the declaration key that marks such a bot; its `setup_task_id` names the task. A go-live waiting for the
computer to get the repository (backend/go_live.py) finishes by itself, so BotOps waits for it; one that failed was
already said to the person who asked, once, so the task may close with BotOps' note.
"""

import json
import sqlite3
from typing import Any, cast

from . import go_live as pending_go_lives
from .store import H, Problem

GOES_LIVE = "goes_live_on_setup"


def _object(value: Any) -> dict[str, Any]:
    try:
        found = json.loads(value) if isinstance(value, str) else value
    except ValueError:
        return {}
    return cast("dict[str, Any]", found) if isinstance(found, dict) else {}


def bot_of(c: sqlite3.Connection, task_id: str) -> tuple[str | None, dict[str, Any]]:
    """The bot whose setup task this is, with its declaration, or (None, {})."""
    row = c.execute(
        "SELECT bot_id, extra FROM bot_settings WHERE json_extract(extra,'$.setup_task_id')=? LIMIT 1", (task_id,)
    ).fetchone()
    if not row:
        return None, {}
    return str(row["bot_id"]), _object(row["extra"])


def pending(c: sqlite3.Connection, bot: str) -> dict[str, Any] | None:
    """The go-live waiting for this bot's repository (backend/go_live.py), or None."""
    found = c.execute("SELECT value FROM registry_metadata WHERE key=?", (pending_go_lives.PREFIX + bot,)).fetchone()
    return _object(found["value"]) if found else None


def require_live(c: sqlite3.Connection, task_id: str) -> None:
    """Refuse marking a seat bot's setup task done while the bot cannot take work yet, saying what finishes it."""
    bot, declared = bot_of(c, task_id)
    if not bot or not declared.get(GOES_LIVE):
        return
    row = cast("dict[str, Any] | None", cast("Any", H).bot(c, bot))
    if not row or row["state"] == "archived":
        return
    if row["state"] == "active":
        return  # live; an onboarding hold is a person's step, not one BotOps can finish
    name, slug = row.get("display_name") or row["slug"], row["slug"]
    waiting = pending(c, bot)
    if waiting and waiting.get("failed"):
        return  # the person who asked was told why it could not go live; the note says the rest
    if waiting:
        where = waiting.get("computer") or "its computer"
        raise Problem(
            "bot_not_live",
            f"{name} is not live yet: Tico finishes its go-live by itself when {where} has its repository. Snooze "
            f"this task for a few minutes and mark it done once {name} is active (`tico bot show {slug}`)",
            409,
        )
    raise Problem(
        "bot_not_live",
        f"{name} is not live yet. Take it live with `tico bot go-live {slug}`, then mark this task done once it is "
        "active",
        409,
    )


def being_set_up(c: sqlite3.Connection, bot: str) -> bool:
    """Whether a planned bot is on its way live: BotOps has its setup task open, or its go-live waits for its
    repository. Such a bot's tasks wait on its setup, not on a person."""
    found = pending(c, bot)
    if found is not None and not found.get("failed"):
        return True
    task = c.execute("SELECT json_extract(extra,'$.setup_task_id') FROM bot_settings WHERE bot_id=?", (bot,)).fetchone()
    return bool(
        task and task[0] and c.execute("SELECT 1 FROM tasks WHERE id=? AND status='open'", (task[0],)).fetchone()
    )
