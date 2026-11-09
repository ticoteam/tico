"""Approvals and asks nobody will act on leave a person's Needs you.

Run hourly (backend/scheduler.py). New ones leave as it happens (a task's end, a replacing request:
hubdb._set_status and hubdb.approval_request); this sweep catches the rest:

1. An undecided approval older than APPROVAL_STALE_DAYS is withdrawn. The bot is told quietly and may ask again.
2. An undecided approval whose task already ended is withdrawn.
3. Questions to people end with their tasks (hubdb.supersede_ask_cards): a question task is a subtask or blocker of the
   task it is about, so nothing about them is swept here.
4. A Confirm card asked of someone for another person (backend/settings_asks.py) that nobody tapped within a day
   expires: its request is declined and the asker told.
5. A release's undecided publish approval at or below the newest release the hub knows shipped is withdrawn
   (backend/release_flow.py superseded_by_shipped). Approvals asked since then are withdrawn as each release publishes.
6. An org rule's held message nobody decided within 24 hours is kept unsent, and its bot is told
   (backend/org_rule_check.py expire_unreviewed). A held message is never sent by a sweep.
"""

from . import hubdb as H
from .batch_work import isolated

ENDED = ("done", "closed", "declined")


def sweep(conn, at=None):
    at = at or H.now()
    cutoff = H.shift(at, days=-H.APPROVAL_STALE_DAYS)
    stale, ended = [], []
    marks = ",".join("?" * len(ENDED))
    for row in H._rows(
        conn.execute(
            "SELECT a.id, a.created_at, t.status FROM approvals a LEFT JOIN tasks t ON t.id=a.task_id "
            f"WHERE a.decision IS NULL AND (a.created_at<? OR t.status IN ({marks})) ORDER BY a.created_at",
            (cutoff, *ENDED),
        )
    ):
        with isolated(conn, "approval_expiry", row["id"]):
            if row["status"] in ENDED:
                if H.approval_withdraw(conn, row["id"], f"Its task was {H.TASK_ENDED[row['status']]}."):
                    ended.append(row["id"])
            elif H.approval_withdraw(
                conn, row["id"], f"No decision in {H.APPROVAL_STALE_DAYS} days. Ask again if it is still needed."
            ):
                stale.append(row["id"])
    from . import release_flow

    superseded = release_flow.superseded_by_shipped(conn)
    from . import settings_asks

    from . import org_rule_check

    held = org_rule_check.expire_unreviewed(conn, at)
    return {
        "stale": stale,
        "ended": ended,
        "superseded": superseded,
        "cards": settings_asks.sweep(conn, at),
        "held": held,
    }
