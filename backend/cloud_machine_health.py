# pyright: strict
"""BotOps's task when an automatic restart of a bot's own cloud machine is refused: machine upkeep is BotOps's, and
BotOps decides whether to ask a person."""

import logging
import sqlite3
from datetime import datetime, timezone
from typing import Any, cast

from . import hubdb

H: Any = hubdb

log = logging.getLogger(__name__)


def file_card(c: sqlite3.Connection, bot: str, tried: str, at: float) -> str | None:
    """One open BotOps task per bot (the held-runs review's way, execution._hand_to_maintainer): the new task's id, or
    None when one is still open, or the company has no BotOps."""
    maintainer = cast(str, H.FLEET_MAINTAINER)
    if bot == maintainer or not H.bot(c, maintainer):
        log.warning("No BotOps to tell that %s's cloud machine restart was refused (%s)", bot, tried)
        return None
    info = cast(dict[str, Any], H.bot(c, bot) or {})
    name = str(info.get("slug") or bot)
    title = f"Check {name}'s cloud machine"[:200]
    owner = cast(str, H.bot_actor(maintainer))
    found = cast(list[dict[str, Any]], H.bot_review_tasks(c, bot, owner, title))
    if any(task["status"] == "open" for task in found):
        return None
    when = datetime.fromtimestamp(at, timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    body = (
        f"{name}'s own cloud machine was offline at {when} with work waiting. Tico stopped it to restart it, and HQ "
        f"refused the start ({tried}). Tico will not replace the machine.\n"
        "Replace the machine, or check it.\n\n" + str(H.BOT_REVIEW_ID.format(id=bot))
    )
    try:
        made = cast(dict[str, Any], H.task_create(c, H.KEEPER, title, body, owner, lint=False, deduplicate=False))
        return str(made["id"])
    except H.Refused:
        log.warning("Could not file the cloud-machine task for %s", bot)
        return None
