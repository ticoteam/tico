"""A person's message routine turns the shared Assistant holds name that person as their requester (#4851).

Before this, such a turn's requester was the Assistant itself, so its person could not read it. Each open or closed
private task the Assistant owns and requested, filed by an automation (`automation_holds.ref`), takes the person its
routine works for when its provenance names exactly one: the routine's owner when a person, else the one person whose
email is the inbox that starts it. Any other row is left as it is. Data only.
"""

import json
import os


def up(conn):
    assistant = "bot:" + (os.environ.get("TICO_ASSISTANT_BOT", "").strip() or "assistant")
    rows = conn.execute(
        "SELECT t.id, a.owner_id, a.flow FROM tasks t JOIN automation_holds h ON h.ref=t.id "
        "JOIN automations a ON a.id=h.automation_id "
        "WHERE t.owner_id=? AND t.requester_id=? AND coalesce(t.private,1)<>0",
        (assistant, assistant),
    ).fetchall()
    for task_id, owner, flow in rows:
        person = _person(conn, owner, flow)
        if person:
            conn.execute("UPDATE tasks SET requester_id=? WHERE id=?", (person, task_id))


def _person(conn, owner, flow):
    """The one person a routine works for, or None when its provenance names none or more than one."""
    owner = str(owner or "")
    if owner.startswith(("person:", "human:")):
        found = conn.execute("SELECT id FROM humans WHERE id=?", (owner.split(":", 1)[1],)).fetchall()
        return "human:" + found[0][0] if len(found) == 1 else None
    try:
        trigger = (json.loads(flow or "{}") or {}).get("trigger") or {}
    except (ValueError, AttributeError):
        return None
    inbox = str(trigger.get("inbox") or "").strip().lower() if isinstance(trigger, dict) else ""
    if not inbox:
        return None
    found = conn.execute("SELECT id FROM humans WHERE lower(email)=?", (inbox,)).fetchall()
    return "human:" + found[0][0] if len(found) == 1 else None
