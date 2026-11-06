"""A bot's question nobody answered goes back to the bot: "still needed?" (Decide cards, Chris, Oct 21, 2026).

A question task (backend/question_tasks.py) with no "if you don't answer by" date (backend/ask_deadlines.py closes
those on their date) that is still unanswered at its due date, or two business days after it was asked (Mon-Fri, where
the team keeps its calendar), closes quietly and goes back to the bot that asked it. Once each weekday morning, run
from the hourly sweep (backend/scheduler.py), every asker bot's questions that crossed the line are recorded in one
`ask.still_needed` event (target: the bot). They reach the bot as lines on its task roundup (backend/task_roundup.py),
never as a wake of their own. The bot asks again (a fresh question, a new clock) or drops it.
"""

from datetime import datetime, time, timedelta, timezone

from . import ask_deadlines, team_rules
from . import hubdb as H
from .batch_work import isolated
from .updates import ZONE

BUSINESS_DAYS = 2
MORNING = time(8)           # the batch runs at the first hourly sweep after this, Monday to Friday
ACTION = "ask.still_needed"


def _local(ts):
    at = H.parse_ts(ts)
    if at.tzinfo is None:
        at = at.replace(tzinfo=timezone.utc)
    return at.astimezone(ZONE)


def crossed_at(asked_ts, days=BUSINESS_DAYS):
    """When an ask written at `asked_ts` has waited `days` business days (BUSINESS_DAYS: the line the weekday batch sends
    it back at; the Task Manager's report uses 1). One asked on a weekend starts counting on Monday morning.
    Friday 10:00 -> Tuesday 10:00."""
    at = _local(asked_ts)
    if at.weekday() >= 5:
        at = datetime.combine(at.date() + timedelta(days=7 - at.weekday()), time(0), ZONE)
    left = days
    while left:
        at += timedelta(days=1)
        if at.weekday() < 5:
            left -= 1
    return at


def batch_due(at):
    """Whether it is a weekday morning's batch time: on or after MORNING, Monday to Friday. `sweep` runs it once a day
    (_ran_today)."""
    local = _local(at)
    return local.weekday() < 5 and local.time() >= MORNING


def _ran_today(conn, at):
    day = _local(at).date().isoformat()
    return conn.execute("SELECT 1 FROM events WHERE action=? AND target=? LIMIT 1", (ACTION + ".batch", day)).fetchone()


def crossed_tasks(conn, at):
    """Open question tasks (backend/question_tasks.py) with no fallback date that crossed the line by `at`: their due
    date when they have one, else two business days after they were asked. Oldest first."""
    from . import question_tasks
    rules = team_rules.load(conn)
    limit = _local(at)
    out = []
    for row in question_tasks.open_rows(conn):
        if ask_deadlines.fallback_at(question_tasks.as_ask(row), rules):
            continue
        due = H.parse_ts(row.get("due_at")) if row.get("due_at") else None
        line = (due if due.tzinfo else due.replace(tzinfo=timezone.utc)) if due else crossed_at(
            question_tasks.asked_at(row))
        if line <= limit:
            out.append(row)
    return out


def send_back(conn, bot, tasks):
    """Take question `tasks` off their people's lists and record them for `bot`'s task roundup (no wake: the roundup
    lists them). Each closes; the bot's task blocked on it goes on without a wake."""
    from . import question_tasks
    people = set()
    for row in tasks:
        people.add(row["owner_id"])
        question_tasks.send_back(conn, row, "No answer in two business days: back to the bot that asked, as still "
                                 "needed?")
    for person in people:
        H._recount(conn, person)
    H._recount(conn, bot)
    H.event(conn, H.KEEPER, ACTION, bot, {"asks": [row["id"] for row in tasks]})


def sweep(conn, at=None):
    """The weekday-morning batch: {bot: [question task ids]} sent back, or {} when it is not that time or already ran
    today."""
    at = at or H.now()
    if not batch_due(at) or _ran_today(conn, at):
        return {}
    tasks_by_bot = {}
    for row in crossed_tasks(conn, at):
        tasks_by_bot.setdefault(row["requester_id"], []).append(row)
    sent = {}
    for bot, tasks in tasks_by_bot.items():
        with isolated(conn, "still_needed", bot):
            send_back(conn, bot, tasks)
            sent[bot] = [row["id"] for row in tasks]
    H.event(conn, H.KEEPER, ACTION + ".batch", _local(at).date().isoformat(),
            {"bots": len(sent), "asks": sum(len(v) for v in sent.values())})
    return sent
