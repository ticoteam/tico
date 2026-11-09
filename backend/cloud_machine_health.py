# pyright: strict
"""Person-facing Health cards for cloud-machine recovery that needs an operator."""

import logging
import sqlite3
from datetime import datetime, timezone
from typing import Any, Callable, cast

from . import hubdb, usage_limits

H: Any = hubdb

log = logging.getLogger(__name__)


def file_card(c: sqlite3.Connection, bot: str, tried: str, at: float, owner_email: str = "") -> str | None:
    """File one deduplicated Health card for the bot's operator after an automatic start is refused."""
    recipients: Callable[..., dict[str, Any] | None] = usage_limits.recipients  # pyright: ignore[reportUnknownMemberType, reportUnknownVariableType]
    person = recipients(c, bot, owner_email)
    if not person:
        return None
    info = cast(dict[str, Any], H.bot(c, bot) or {})
    name = str(info.get("display_name") or info.get("slug") or bot)
    when = datetime.fromtimestamp(at, timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    title = f"Health: Check {name}'s cloud machine"[:200]
    body = (
        f"Tico tried to restart {name}'s own cloud machine at {when} because runnable work was waiting while it was offline. "
        f"HQ refused the start ({tried}). Tico will not replace the machine.\n\n"
        "Choose one:\n"
        "- Press Replace machine in Computers.\n"
        "- Check the machine."
    )
    try:
        made = cast(dict[str, Any], H.task_create(c, H.KEEPER, title, body, H.human_actor(person["id"]), lint=False))
        return str(made["id"])
    except H.Refused:
        log.warning("Could not file the cloud-machine Health card for %s", bot)
        return None
