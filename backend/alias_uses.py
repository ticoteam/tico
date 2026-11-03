"""Calls that still use an old name (`hub` for `tico`, and old tool names), so a team can see who still needs fixing.

A `hub` call says so on its first request (the X-Tico-Alias header, clients/tico.py). Each such request writes one
`alias.hub_used` event: the caller, the form (`cli` or `mcp`), its bot and the command words without values. Health
reads these events (`alias_report`); nothing else does, and no event goes to a webhook or a routine.
"""
from . import hubdb as H

ACTION = "alias.hub_used"
FORMS = ("cli", "mcp")
ALIAS_HEADER = "x-tico-alias"
COMMAND_HEADER = "x-tico-alias-command"


def form_of(headers):
    """The form the old name came in by, or "" when the request does not say it used an old name."""
    form = (headers.get(ALIAS_HEADER) or "").strip().lower()
    return form if form in FORMS else ""


def record(c, identity, form, command):
    """One event for an old-name call. The bot is the caller's own bot, "" for a person or a script."""
    bot = identity.actor[len("bot:"):] if identity.actor.startswith("bot:") else ""
    command = " ".join(str(command or "").split())[:80]
    H.event(c, identity.actor, ACTION, command,
            {"form": form, "bot": bot, "command": command, "computer": identity.runner_id or ""})
