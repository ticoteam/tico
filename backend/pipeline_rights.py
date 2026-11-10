# pyright: strict
"""Who an actor reference names, and who the company's owner is.

A pipeline's owner and manager are stored as references, and a reference may name a bot by its slug or its id, or a
person as `person:<id>`. `canonical` reads each one as the actor string a reader's identity carries, so the comparison
is the same whichever form was stored. Who may change a pipeline is `pipeline_settings.may_change`.
"""

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import sqlite3

from . import access as Access


def canonical(c: "sqlite3.Connection", ref: object) -> str:
    """The actor string a stored reference names: `human:<id>` for a person (`person:<id>` too), `bot:<id>` for a bot named
    by its id or its slug, and "" for nothing."""
    text = str(ref or "").strip()
    if text.startswith("person:"):
        return "human:" + text[len("person:") :].strip()
    if text.startswith("human:"):
        return text
    value = (text[len("bot:") :] if text.startswith("bot:") else text).strip()
    if not value:
        return ""
    found = c.execute("SELECT id FROM bots WHERE id=? OR slug=? LIMIT 1", (value, value)).fetchone()
    return "bot:" + str(found[0] if found else value)


def company_owner(c: "sqlite3.Connection") -> str:
    """The company owner's actor (`human:<id>`): the person whose address is the owner's (access.owner_email), or "" when no
    person on the roster has it."""
    email = Access.owner_email(c)
    found = c.execute("SELECT id FROM humans WHERE lower(email)=? LIMIT 1", (email,)).fetchone() if email else None
    return "human:" + str(found[0]) if found else ""
