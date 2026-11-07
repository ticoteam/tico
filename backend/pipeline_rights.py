# pyright: strict
"""Who may change a pipeline's rules: its stage requirements, their prefix and the pipeline's settings. That is the
pipeline's owner (task_types.pipeline_owner_id, the actor who made it) and the company's owner and Admins
(access.owner_and_admin_emails). Bots never edit rules."""

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import sqlite3

from . import access as Access


def may_edit_rules(c: "sqlite3.Connection", actor: str, pipeline_id: str | None) -> bool:
    """True when `actor` may change the rules of pipeline `pipeline_id`."""
    who = str(actor or "")
    if not who or not pipeline_id:
        return False
    row = c.execute("SELECT pipeline_owner_id FROM task_types WHERE id=?", (pipeline_id,)).fetchone()
    if row is None:
        return False
    if row[0] and who == row[0]:
        return True
    if not who.startswith("human:"):
        return False
    found = c.execute("SELECT email FROM humans WHERE id=?", (who.split(":", 1)[1],)).fetchone()
    email = str((found[0] if found else "") or "").strip().lower()
    return bool(email) and email in set(Access.owner_and_admin_emails(c))
