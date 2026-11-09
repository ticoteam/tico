"""Deleting a person (spec "Deleting a person"): who may erase, the reason code, the erase, and the audit row that records it.

The rows are removed by store.erase_rows, in the caller's transaction, and the identifiers it erases are suppressed there
(as salted hashes) so that sync and ingest cannot create the person again. This module decides whether the actor may erase
and writes the audit row. The audit row names what was removed by count, never by name, id or identifier.
"""

# pyright: strict

import re
import sqlite3
from typing import Any

from backend import access as A
from backend import audit_log
from backend import hubdb as H
from backend.entities import store as ES
from backend.entities import visible as V

REASON = re.compile(r"[a-z][a-z_]{2,39}")


def may_erase(c: sqlite3.Connection, actor: str) -> bool:
    """The owner and the Admins, as people. A bot, the Assistant acting for nobody, and the system may not erase."""
    key = V.actor_key(c, actor)
    if not key.startswith("human:"):
        return False
    row = c.execute("SELECT email FROM humans WHERE id = ?", (key.removeprefix("human:"),)).fetchone()
    email = str(row[0] or "").strip().lower() if row else ""
    return bool(email) and email in A.owner_and_admin_emails(c)  # pyright: ignore[reportUnknownMemberType]


def erase(c: sqlite3.Connection, actor: str, entity_id: str, reason: str) -> dict[str, Any]:
    """Erases the entity and everything that names it. `reason` is a short code (lowercase letters and underscores, such as
    `request`): the audit row keeps it, so free text, which may name the person, is refused. Returns the entity's type and
    the rows removed per table."""
    if not may_erase(c, actor):
        H.refuse(c, actor, "forbidden", "Only the owner or an admin can erase a person; ask one of them")  # pyright: ignore[reportUnknownMemberType]
    code = str(reason or "").strip()
    if not REASON.fullmatch(code):
        H.refuse(  # pyright: ignore[reportUnknownMemberType]
            c, actor, "kind", "Give the reason as a short code: lowercase letters and underscores, such as request"
        )
    result = ES.erase_rows(c, actor, str(entity_id), code)
    audit_log.write(  # pyright: ignore[reportUnknownMemberType]
        c,
        actor,
        f"directory_erase:{H.new_id()}",
        None,
        {
            "entity_type": result["type"],
            "reason": code,
            "removed": result["removed"],
            "suppressed": result["suppressed"],
        },
    )
    return result
