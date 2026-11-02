"""One request to a bot to restate, in plain words, what it needs from a person it set a task waiting on.

A task set waiting on a person before the plain-ask rule (hubdb.require_person_ask) may carry no ask, or the bot's
own shorthand as one. Its card waits at the bottom of the person's Needs you, marked as waiting for the bot to
explain (views.person_ask), and its bot is asked once to restate it. The `task.ask_restate` event is the durable
marker: a task is asked about once, ever. The bot's new waiting note clears the card by passing the lint, so
nothing here needs undoing. Run by the scheduler, never on a read.
"""

from . import hubdb as H
from .batch_work import isolated

ACTION = "task.ask_restate"


def unexplained(conn):
    """Tasks a bot set waiting on a person with no plain ask and no open question to them, not yet asked about."""
    out = []
    for row in H._rows(conn.execute(
            "SELECT * FROM tasks t WHERE t.status='waiting' AND t.waiting_on LIKE 'human:%' AND t.owner_id LIKE 'bot:%' "
            "AND NOT EXISTS (SELECT 1 FROM events e WHERE e.action=? AND e.target=t.id) ORDER BY t.created_at",
            (ACTION,))):
        if not H.lint_person_ask(row.get("note")):
            continue
        if any(a["recipient_id"] == row["waiting_on"] for a in H.open_task_asks(conn, row)):
            continue
        out.append(row)
    return out


def message(row):
    person = H.actor_id(row["waiting_on"])
    return (f"{person} can't tell what you need from them on \"{row['title']}\", so it waits at the bottom of their "
            f"Needs you. Restate it in one or two plain sentences: what you need from {person} and what happens "
            f"after, at most {H.LINT_ASK_WORDS} words, with no ids, hashes, file paths, s3:// links, tool names or error output. "
            f"Set it with: hub task update {row['id']} --status waiting --on {person} --note \"<your ask>\". "
            f"Example: \"{H.LINT_ASK_EXAMPLE}\" If it no longer needs {person}, take it off them instead.")


def ask_restate(conn):
    """Ask each unexplained task's bot, once, to restate its ask. Returns the task ids asked about."""
    asked = []
    for row in unexplained(conn):
        with isolated(conn, "restate_asks", row["id"]):
            H._wake(conn, row, row["owner_id"], message(row), {"wake": "restate_ask"}, trigger="stall_check")
            H.event(conn, H.KEEPER, ACTION, row["id"], {"bot": H.actor_id(row["owner_id"]), "to": row["waiting_on"]})
            asked.append(row["id"])
    return asked
