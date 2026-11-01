"""A bot's question nobody answered goes back to the bot: "still needed?" (Decide cards, Chris, Oct 21, 2026).

A question from a bot to a person with no "if you don't answer by" date (backend/ask_deadlines.py closes those on
their date) that is still unanswered two business days after it was asked (Mon-Fri, where the team keeps its
calendar) leaves the person's Needs you and goes back to the bot that asked it. Once each weekday morning, run from
the hourly sweep (backend/scheduler.py), every asker bot gets ONE wake listing all of its questions that crossed the
line, never one per question. The bot asks again (a fresh question, a new clock) or drops it.
"""

from datetime import datetime, time, timedelta, timezone

from . import ask_deadlines, team_rules
from . import hubdb as H
from .batch_work import isolated
from .updates import ZONE

BUSINESS_DAYS = 2
MORNING = time(8)           # the batch runs at the first hourly sweep after this, Monday to Friday
ACTION = "ask.still_needed"
LISTED = 20                 # questions written out in one wake; the rest are counted


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


def crossed(conn, at):
    """Open asks from bots to people with no fallback date that crossed the line by `at`, oldest first."""
    rules = team_rules.load(conn)
    limit = _local(at)
    out = []
    for asked, task in ask_deadlines.open_asks(conn, at):
        if ask_deadlines.fallback_at(asked, rules) or (asked.get("refs") or {}).get("stuck"):
            continue
        if crossed_at(H.ask_clock(asked)) <= limit:
            out.append((asked, task))
    return out


def _line(asked, task):
    who = H.actor_id(asked["recipient_id"])
    where = f' on "{H._clip(task["title"], 80)}" ({task["id"]})' if task else ""
    return f'- To {who}{where}: "{H._clip(asked.get("body") or "", 160)}" (asked {str(H.ask_clock(asked))[:10]})'


def send_back(conn, bot, items):
    """Take `items` off their people's lists and wake `bot` once with all of them."""
    stamp = H.now()
    people = set()
    for asked, task in items:
        conn.execute("UPDATE messages SET superseded_at=? WHERE id=?", (stamp, asked["id"]))
        person = asked["recipient_id"]
        people.add(person)
        if task:
            H._task_event(conn, task["id"], H.KEEPER, "asks_superseded", None, "1",
                          "No answer in two business days: back to the bot that asked, as still needed?")
            # The wait was for this answer: the next move is the bot's, and the task leaves the person's Needs you,
            # unless another question to them is still open on it.
            H.drop_asked_waits(conn, task["id"], [asked["id"]], "Still needed? went to the bot")
            # The question shows it went back unanswered, and a bot's task left with nothing to wait on goes back to
            # open; the one notice below is the bot's word of it (backend/task_holds.py).
            from . import task_holds
            task_holds.withdrawn(conn, task["id"], [asked["id"]], "no answer in two business days (still needed?)",
                                 told=False)
    lines = [_line(asked, task) for asked, task in items[:LISTED]]
    if len(items) > LISTED:
        lines.append(f"- and {len(items) - LISTED} more")
    n = len(items)
    text = (f"Still needed? {n} of your question{'s' if n != 1 else ''} to people had no answer in two business days, "
            f"so {'they have' if n != 1 else 'it has'} left their Needs you:\n" + "\n".join(lines) +
            "\n\nFor each one: if you still need it, ask again as a fresh question (hub task ask, or hub task question "
            "for picks), short and plain, with what happens if they don't answer by a date. Otherwise drop it and carry on "
            "without it. Aim for questions a person can answer within 24 hours.")
    msg = H.say(conn, H.KEEPER, bot, text, kind="notice",
                refs={"wake": "still_needed", "asks": [asked["id"] for asked, _ in items]})
    for person in people:
        H._recount(conn, person)
    H._recount(conn, bot)
    H.event(conn, H.KEEPER, ACTION, bot, {"asks": [asked["id"] for asked, _ in items], "message": msg["id"]})
    return msg


def sweep(conn, at=None):
    """The weekday-morning batch: {bot: [ask ids]} sent back, or {} when it is not that time or already ran today."""
    at = at or H.now()
    if not batch_due(at) or _ran_today(conn, at):
        return {}
    by_bot = {}
    for asked, task in crossed(conn, at):
        by_bot.setdefault(asked["sender_id"], []).append((asked, task))
    sent = {}
    for bot, items in by_bot.items():
        with isolated(conn, "still_needed", bot):
            send_back(conn, bot, items)
            sent[bot] = [asked["id"] for asked, _ in items]
    H.event(conn, H.KEEPER, ACTION + ".batch", _local(at).date().isoformat(),
            {"bots": len(sent), "asks": sum(len(v) for v in sent.values())})
    return sent
