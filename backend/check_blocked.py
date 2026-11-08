"""A blocked Review or QA check shows on its ticket (Dev 553).

A check is a subtask of a Dev ticket requesting one of its board's check steps (Review 1, Review 2 or QA on the Dev
board: backend/step_flow.titled), owned by a bot. It is blocked when it is Waiting or
waits on an open blocker. Its ticket then shows the hold (backend/task_blocked.py) with the check's reason, and the
ticket's builder gets one notice in the ticket's own room per blocked episode, never Head of Engineering. The ticket
stays at its step while held. When the check works again, or finishes, the hold clears and the ticket is where it was.
"""
import re

from . import review_gate

GENERIC = "Waiting on a hold"       # task_blocked.transition's reason when a wait had no note
# A person wait's note starts "Blocked on Ana: "; the ticket's reason names who it waits on at its end instead.
PERSON_PREFIX = re.compile(r"^Blocked on [^:]{1,120}: ")
# A reason Tico derived from the check's holds names its holder first (backend/task_holds.py); the ticket names it last.
HOLDER_PREFIX = re.compile(r"^Waiting on [^:]{1,120}(: |$)")


def _H():
    from . import hubdb
    return hubdb


def kind(c, row, ticket=None):
    """The label of the board's check step a check's title asks for ('Review 1', 'QA', ...), else None."""
    from . import step_flow
    found = step_flow.child_check(c, row, ticket) if row else None
    return step_flow.label(found) if found else None


def _name(c, actor):
    H = _H()
    if H.is_human(actor):
        return H.person_name(c, actor)
    if H.is_bot(actor):
        return (H.bot(c, H.actor_id(actor)) or {}).get("display_name") or H.actor_id(actor)
    return None


def blocked(c, child):
    """{"why", "on"} when this check is blocked, else None."""
    H = _H()
    from . import task_relations as TR
    if child["status"] == "waiting":
        detail = H.wait_detail(c, child) or {}
        why = PERSON_PREFIX.sub("", str(child.get("blocked_reason") or "").strip())
        if child.get("blocked_kind") != "manual":
            from .task_holds import NOTHING, LEFT
            why = "" if why.startswith((NOTHING, LEFT)) else HOLDER_PREFIX.sub("", why)
            # Held by nothing Tico tracks: the check's own note says why, as detail beneath the ticket's reason.
            why = why or PERSON_PREFIX.sub("", next((s.strip() for s in str(child.get("note") or "").splitlines()
                                                     if s.strip()), ""))[:300]
        if not why or why == GENERIC:
            why = detail.get("why") or "it is waiting"
        return {"why": why, "on": detail.get("on") or child.get("waiting_on")}
    if child["status"] in H.ACTIVE_STATUSES:
        blocker = H.task(c, TR.live_blocker(c, child["id"], H.ACTIVE_STATUSES) or "")
        if blocker:
            return {"why": f"it waits on {blocker['title']}", "on": blocker["owner_id"]}
    return None


def detail(c, row):
    """A ticket's wait on a blocked check, as wait_detail gives it, with the ticket's hold reason; else None."""
    H = _H()
    from . import profiles, task_relations as TR
    if not row or not review_gate.engineering(c, row):
        return None
    for child_id in TR.child_ids(c, row["id"]):
        child = H.task(c, child_id)
        label = kind(c, child, row)
        # A builder's own decision question can be titled for a review step without being a judging check.
        if not label or not profiles.is_check(c, child):
            continue
        found = blocked(c, child)
        if not found:
            continue
        if H.task_private(c, child) and not H.task_private(c, row):
            # The ticket's readers may not read the check: say only that it is blocked.
            reason = f"{label} blocked"
        else:
            who = _name(c, found["on"]) if found["on"] not in (None, "external", "person") else None
            reason = f"{label} blocked: {found['why']}" + (f", waiting on {who}" if who else "")
        return {"why": reason[:1000], "on": found["on"], "check": child["id"], "reason": reason[:1000]}
    return None


def notify(c, row):
    """The builder hears once per blocked episode of the ticket, in the ticket's room."""
    H = _H()
    row = H.task(c, row["id"])
    since = row.get("blocked_since")
    if not since or row.get("blocked_kind") != "check" or not H.is_bot(row["owner_id"]):
        return None
    if c.execute("SELECT 1 FROM events WHERE action='task.check_blocked' AND target=? "
                 "AND json_extract(detail,'$.since')=?", (row["id"], since)).fetchone():
        return None
    sent = H._wake(c, row, row["owner_id"], f"Blocked: {row['title']}\n{row['blocked_reason']}\n"
                   "It goes back to its step by itself when the check works again.",
                   refs={"news": "check_blocked"}, quiet_bots=True)
    H.event(c, H.KEEPER, "task.check_blocked", row["id"], {"since": since, "message": (sent or {}).get("id")})
    return sent


def apply(c, ticket_id):
    """Hold the ticket on its blocked check, or let its hold clear when no check is blocked any more."""
    from . import task_blocked
    H = _H()
    row = H.task(c, ticket_id)
    found = detail(c, row)
    if found:
        task_blocked.hold(c, ticket_id, found["reason"], kind="check")
        notify(c, row)
    elif row and row.get("blocked_kind") == "check":
        still = task_blocked.wait(c, row)
        if still:
            # Something else still holds it: say that, not the check that works again.
            c.execute("UPDATE tasks SET blocked_kind='dependency',blocked_reason=?,updated_at=? WHERE id=?",
                      (("Waiting on " + still["why"])[:1000], H.now(), ticket_id))
            task_blocked.bump(c, row)
        else:
            task_blocked.reconcile(c, ticket_id)


def changed(c, task_id):
    """A task's status or blockers changed: when it is a check, its ticket follows."""
    H = _H()
    from . import task_relations as TR
    row = H.task(c, task_id)
    if not row or not H.is_bot(row["owner_id"]) or not kind(c, row):
        return
    parent = TR.parent_of(c, task_id)
    if parent:
        apply(c, parent)
