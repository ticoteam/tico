"""Approvals and asks nobody will act on leave a person's Needs you.

Run hourly (backend/scheduler.py). New ones leave as it happens (a task's end, a replacing request:
hubdb._set_status and hubdb.approval_request); this sweep catches the rest:

1. An undecided approval older than APPROVAL_STALE_DAYS is withdrawn. The bot is told quietly and may ask again.
2. An undecided approval whose task already ended is withdrawn.
3. An unanswered ask to a person about a task that ended is superseded, wherever it was asked, and its asker is told
   (backend/task_holds.py). A question on an open task carries on when a task above it ends: it lives and dies with
   its own task (Dev 627). A done task keeps only the question asking its requester to accept what it delivered
   (hubdb.ACCEPTANCE_ASK_SQL).
4. The keeper's old "no progress" questions to people are superseded: a stuck task goes to BotOps now, never to a person
   (hubdb._report_stuck). None are asked any more, so this closes the ones open when that shipped, once.
"""

from . import hubdb as H
from .batch_work import isolated

ENDED = ("done", "closed", "declined")


def sweep(conn, at=None):
    at = at or H.now()
    cutoff = H.shift(at, days=-H.APPROVAL_STALE_DAYS)
    stale, ended = [], []
    marks = ",".join("?" * len(ENDED))
    for row in H._rows(conn.execute(
            "SELECT a.id, a.created_at, t.status FROM approvals a LEFT JOIN tasks t ON t.id=a.task_id "
            f"WHERE a.decision IS NULL AND (a.created_at<? OR t.status IN ({marks})) ORDER BY a.created_at",
            (cutoff, *ENDED))):
        with isolated(conn, "approval_expiry", row["id"]):
            if row["status"] in ENDED:
                if H.approval_withdraw(conn, row["id"], f"Its task was {H.TASK_ENDED[row['status']]}."):
                    ended.append(row["id"])
            elif H.approval_withdraw(conn, row["id"], f"No decision in {H.APPROVAL_STALE_DAYS} days. "
                                                       "Ask again if it is still needed."):
                stale.append(row["id"])
    # Every open ask to a person on a task, with that task's status; few are open at once, so each one's ancestors are
    # walked here rather than in SQL.
    rows = H._rows(conn.execute(
        f"SELECT m.id, t.id AS task_id, t.status, ({H.ACCEPTANCE_ASK_SQL}) AS acceptance, "
        "json_extract(m.refs,'$.stuck') AS stuck, m.sender_id FROM messages m "
        f"JOIN conversations cv ON cv.id=m.conversation_id JOIN tasks t ON t.id={H.MESSAGE_TASK_SQL} "
        f"WHERE m.kind='ask' AND m.recipient_id LIKE 'human:%' AND m.deleted_at IS NULL AND {H.OPEN_ASK_SQL}"))
    gone = [r for r in rows if (r["status"] in ENDED and not r["acceptance"])
            or (r["stuck"] and r["sender_id"] == H.KEEPER)]
    asks = []
    for r in gone:
        # One row at a time, like the approvals: a row that fails stays open for the next sweep without undoing the rest.
        with isolated(conn, "approval_expiry", r["id"]):
            conn.execute("UPDATE messages SET superseded_at=? WHERE id=? AND superseded_at IS NULL", (at, r["id"]))
            H.drop_asked_waits(conn, r["task_id"], [r["id"]], "Its question to them was withdrawn")
            # Its asker is told, and a bot's task left with nothing to wait on goes back to open (task_holds.py).
            from . import task_holds
            task_holds.withdrawn(conn, r["task_id"], [r["id"]], f"its task was {H.TASK_ENDED.get(r['status'], 'ended')}"
                                 if r["status"] in ENDED else "an old no-progress question")
            H._recount(conn, r["sender_id"])
            asks.append(r["id"])
    return {"stale": stale, "ended": ended, "asks": asks}
