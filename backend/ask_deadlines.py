"""A bot's ask to a person does not wait for ever: the bot goes ahead on its default, or the ask expires.

Run hourly (backend/scheduler.py):

Every ask with a date closes on it (Decide cards, Oct 21: "I can just kind of close"); one without a date goes back to its
bot after two business days (backend/still_needed.py).

1. Go ahead. A bot may say what it will do if nobody answers (`if_no_answer` on `hub task ask` and `hub task question`:
   what it does, what kind of step that is, and optionally by when). When the step is reversible and internal and the
   person has said nothing by then (`by` or `go_ahead_days` after the ask, whichever is later: the team's days are the
   least a person gets, and `by` is at most EXPIRE_DAYS away), the keeper answers the ask for them:
   "No answer from Ana by Oct 16, so the bot goes ahead: <what>". The bot wakes on that answer as on any answer. A send,
   a payment or a delete never goes ahead on its own: it waits for an answer, then expires. The owner turns this off
   (`bots_go_ahead`) or changes the days in Settings > Humans (backend/team_rules.py).
   A fallback that may not go ahead (a send, a payment, a delete, or any fallback when the owner turned going ahead
   off) still closes on the date it named: the bot is told that nothing went ahead and decides what to do.
2. Expire. An ask to a person that has gone EXPIRE_DAYS with no word from them closes: with its default when rule 1
   allows it, otherwise the work is dropped. The bot is told which, and why, in the answer. A task a bot handed a person
   (owned by the person, requested by the bot: a Needs-you task, or the small subtask an ask becomes) closes the same
   way when the person has not touched it in EXPIRE_DAYS, unless it is due later or waits on someone else, and the bot
   hears why when it closes.

A task with nobody's ask on it is "Still wanted?" (backend/still_wanted.py), which only a person's answer closes.
"""

from . import hubdb as H
from . import team_rules
from .batch_work import isolated

EXPIRE_DAYS = 14
# At most this many asks settled per sweep, oldest first: each one wakes a bot, so a backlog (the first sweep after
# this shipped) reaches the bots over a few hours instead of all at once.
BATCH = 25
# Only a reversible, internal step goes ahead without an answer.
GOES_AHEAD = "reversible"
WAITS = {"send": "a send", "spend": "spending money", "delete": "a delete"}
# Where the person is still deciding, not just asked: a task they started or handed for review is their own work.
ASKED_STATUSES = ("open", "waiting")


def _name(conn, actor):
    row = (H.human(conn, H.actor_id(actor)) if H.is_human(actor) else H.bot(conn, H.actor_id(actor))) or {}
    return row.get("name") or H.actor_id(actor)


def _day(ts):
    at = H.parse_ts(ts)
    return f"{at:%b} {at.day}" if at else "the date it gave"


def open_asks(conn, at):
    """Unanswered asks from a bot to a person, oldest first; an ask on a task only while the task is live."""
    rows = H._rows(conn.execute(
        "SELECT m.* FROM messages m JOIN conversations cv ON cv.id=m.conversation_id "
        "WHERE m.kind='ask' AND m.from_actor LIKE 'bot:%' AND m.to_actor LIKE 'human:%' AND m.deleted_at IS NULL "
        f"AND {H.OPEN_ASK_SQL} AND (m.expires_at IS NULL OR m.expires_at>?) ORDER BY m.created", (at,)))
    out = []
    for row in rows:
        asked = H.message(conn, row["id"])
        task_id = H.message_task_id(asked, H.conversation(conn, asked["conversation_id"]))
        task = H.task(conn, task_id) if task_id else None
        if task_id and (not task or task["status"] not in H.ACTIVE_STATUSES):
            continue
        out.append((asked, task))
    return out


def last_word(conn, person, asked, task):
    """When `person` last said or did anything on this ask: asked it, wrote in its conversation, or changed its task."""
    clock = H.ask_clock(asked)
    said = conn.execute("SELECT max(created) FROM messages WHERE conversation_id=? AND from_actor=? AND created>?",
                        (asked["conversation_id"], person, clock)).fetchone()[0]
    did = conn.execute("SELECT max(ts) FROM task_events WHERE task_id=? AND actor=? AND ts>?",
                       (task["id"], person, clock)).fetchone()[0] if task else None
    return max(x for x in (clock, said, did) if x)


def deadline(asked, rules):
    """When this ask goes ahead on its default, or None when it never does on its own."""
    default = (asked.get("refs") or {}).get("if_no_answer") or {}
    if not rules["bots_go_ahead"] or default.get("kind") != GOES_AHEAD or not default.get("do"):
        return None
    least = H.shift(H.ask_clock(asked), days=rules["go_ahead_days"])
    return max(default.get("by") or least, least)


def fallback_at(asked, rules):
    """When this ask closes on its "if you don't answer by" fallback: the day it goes ahead (`deadline`), or, for a
    fallback that never goes ahead on its own, the date it named. None when it has no date: it goes back to its bot
    after two business days instead (backend/still_needed.py)."""
    default = (asked.get("refs") or {}).get("if_no_answer") or {}
    return deadline(asked, rules) or (default.get("by") if default.get("do") else None)


def _picked(asked, default):
    """The option the default names, as a structured answer, when the ask has one question and the default is one of its
    options ("Yes"): the bot reads it exactly as if the person had picked it."""
    questions = (asked.get("refs") or {}).get("questions") or []
    if len(questions) != 1:
        return None
    wanted = str(default.get("do") or "").strip().casefold()
    for option in questions[0].get("options") or []:
        if str(option.get("label") or "").strip().casefold() == wanted:
            return {questions[0]["id"]: [option["label"]]}
    return None


def settle(conn, asked, task, text, outcome, *, answers=None):
    """Answer the ask as the keeper with `text`, which wakes the bot that asked, and take it off the person's list."""
    conv = H.conversation(conn, asked["conversation_id"])
    refs = {"depth": (asked.get("refs") or {}).get("depth", 1), "if_no_answer": outcome}
    if task:
        refs.update({"task": task["id"], "comment": True})
    if answers:
        refs["answer"] = {"target": {"comment": asked["id"]}, "answers": answers, "other": None, "dismiss": False,
                          "by": H.KEEPER, "at": H.now()}
    H._write_message(conn, H.KEEPER, asked["from_actor"], text, conv, "answer", refs, asked["id"], None)
    person = asked["to_actor"]
    if task:
        conn.execute("UPDATE tasks SET updated=? WHERE id=?", (H.now(), task["id"]))
        if task.get("waiting_on") == person:
            # The wait was for this answer: the next move is the bot's, and the task leaves the person's Needs you.
            conn.execute("UPDATE tasks SET waiting_on=NULL WHERE id=?", (task["id"],))
            H._task_event(conn, task["id"], H.KEEPER, "waiting_on", person, None, text)
    H.event(conn, H.KEEPER, "ask." + outcome, asked["id"],
            {"to": person, "bot": asked["from_actor"], **({"task": task["id"]} if task else {})})
    H._recount(conn, person)
    H._recount(conn, asked["from_actor"])


def sweep(conn, at=None):
    """Go ahead on, or expire, every ask that is due. Returns {"went_ahead": [...], "expired": [...], "closed": [...]}:
    ask message ids, and task ids for closed tasks."""
    at = at or H.now()
    rules = team_rules.load(conn)
    expiry = H.shift(at, days=-EXPIRE_DAYS)
    done = {"went_ahead": [], "expired": [], "closed": []}
    settled = lambda: sum(len(v) for v in done.values())     # noqa: E731
    for asked, task in open_asks(conn, at):
        if settled() >= BATCH:
            return done
        person, bot = asked["to_actor"], asked["from_actor"]
        default = (asked.get("refs") or {}).get("if_no_answer") or {}
        spoke = last_word(conn, person, asked, task)
        due = deadline(asked, rules)
        with isolated(conn, "ask_deadlines", asked["id"]):
            if due and due <= at and spoke == H.ask_clock(asked):
                settle(conn, asked, task, f"No answer from {_name(conn, person)} by {_day(due)}, so "
                       f"{_name(conn, bot)} goes ahead with what it said it would do: {default['do']}",
                       "went_ahead", answers=_picked(asked, default))
                done["went_ahead"].append(asked["id"])
            elif (closes := fallback_at(asked, rules)) and closes <= at and spoke == H.ask_clock(asked):
                # Its date passed, but what it would do does not go ahead without an answer: the question still closes,
                # and the bot decides what to do without it.
                held = WAITS.get(default.get("kind"))
                reason = (f"{default['do']} is {held}, which never goes ahead without an answer" if held else
                          f"the team does not let bots go ahead on their own, so {default['do']} does not happen")
                settle(conn, asked, task, f"No answer from {_name(conn, person)} by {_day(closes)}, so this question "
                       f"closed. What {_name(conn, bot)} said it would do: {reason}. Drop the work it was for, or ask "
                       "again if it still matters.", "expired")
                done["expired"].append(asked["id"])
            elif spoke < expiry:
                why = f"No answer from {_name(conn, person)} in {EXPIRE_DAYS} days, so this ask expired."
                if due and due <= at:
                    settle(conn, asked, task, f"{why} {_name(conn, bot)} goes ahead with what it said it would do: "
                           f"{default['do']}", "expired", answers=_picked(asked, default))
                else:
                    held = WAITS.get(default.get("kind"))
                    reason = (f" What it would have done ({default['do']}) is {held}, which never goes ahead without an "
                              "answer." if held and default.get("do") else "")
                    settle(conn, asked, task, f"{why}{reason} Drop the work it was for, or ask again if it still matters.",
                           "expired")
                done["expired"].append(asked["id"])
    for task in handed_to_people(conn, expiry, at):
        if settled() >= BATCH:
            break
        with isolated(conn, "ask_deadlines", task["id"]):
            note = (f"No answer from {_name(conn, task['owner'])} in {EXPIRE_DAYS} days, so this ask expired and "
                    f"closed. {_name(conn, task['requester'])}: drop the work it was for, or ask again if it still matters.")
            H.task_close(conn, H.KEEPER, task["id"], note)
            H.event(conn, H.KEEPER, "ask.expired", task["id"], {"to": task["owner"], "bot": task["requester"]})
            done["closed"].append(task["id"])
    return done


def handed_to_people(conn, expiry, at=None):
    """Tasks a bot handed a person that the person has not touched since `expiry`: owned by them, requested by a bot,
    still open or waiting (on nobody or that bot), not due later, not on a numbered board, not set aside until a date."""
    marks = ",".join("?" * len(ASKED_STATUSES))
    rows = H._rows(conn.execute(
        f"SELECT t.* FROM tasks t LEFT JOIN task_types y ON y.id=t.type_id WHERE t.status IN ({marks}) "
        "AND t.owner LIKE 'human:%' AND t.requester LIKE 'bot:%' AND t.created<? AND coalesce(y.numbered,0)=0 "
        "ORDER BY t.created", (*ASKED_STATUSES, expiry)))
    out = []
    for row in rows:
        if (H.TS.snoozed(row, at or H.now()) or (row.get("due") and str(row["due"])[:10] >= (at or H.now())[:10])
                or row.get("waiting_on") not in (None, "", row["requester"])):
            continue
        person = row["owner"]
        touched = conn.execute(
            "SELECT max(ts) FROM (SELECT max(ts) AS ts FROM task_events WHERE task_id=? AND actor=? "
            "UNION ALL SELECT max(created) FROM messages WHERE conversation_id=? AND from_actor=?)",
            (row["id"], person, row["conversation_id"], person)).fetchone()[0]
        if not touched or touched < expiry:
            out.append(row)
    return out
