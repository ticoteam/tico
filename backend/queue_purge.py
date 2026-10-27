"""Clear queued automatic wakes and waits without touching tasks or work already running.

A person's message stays queued, including a keeper notice of that person's own act
(`refs.caused_by`). A job already leased or running finishes. Cancelling the job leaves
the task on its current step.
"""

from . import usage_limits, wake_rules
from .store import H, Problem

RUNNING = ("leased", "running", "input")


def bots_for(c, scope):
    """The bots a purge covers: the whole team, or one group and the groups under it."""
    default = usage_limits.company(c)
    if scope == usage_limits.COMPANY:
        return sorted(usage_limits.members(c, scope, default))
    group = scope.split(":", 1)[1] if scope.startswith("group:") and scope.count(":") == 1 else ""
    if not group or group not in usage_limits.groups(c, default)[0]:
        raise Problem("not_found", "Group not found", 404)
    return sorted(usage_limits.members(c, scope, default))


def _from_a_person(row):
    refs = H._json(row["refs_json"], {}) or {}
    return H.is_human(row["from_actor"]) or H.is_human(str(refs.get(wake_rules.CAUSED_BY) or ""))


def apply(c, bots):
    """Cancel this scope's queued automatic jobs. Returns counts only, never message text."""
    if not bots:
        return {"cancelled": 0, "kept_people": 0, "still_running": 0}
    marks = ",".join("?" * len(bots))
    still_running = c.execute(
        f"SELECT count(*) FROM jobs WHERE bot IN ({marks}) AND state IN ({','.join('?' * len(RUNNING))})",
        (*bots, *RUNNING)).fetchone()[0]
    rows = c.execute(
        f"""SELECT j.id, m.from_actor, m.refs_json FROM jobs j JOIN messages m ON m.id=j.message_id
            WHERE j.state='queued' AND j.bot IN ({marks})""", bots).fetchall()
    cancel, kept_people = [], 0
    for row in rows:
        if _from_a_person(row):
            kept_people += 1
        else:
            cancel.append(row["id"])
    cancelled = 0
    if cancel:
        cancelled = c.execute(
            f"UPDATE jobs SET state='cancelled', held=0 WHERE state='queued' AND id IN ({','.join('?' * len(cancel))})",
            cancel).rowcount
    return {"cancelled": cancelled, "kept_people": kept_people, "still_running": still_running}
