"""Failed runs wait, then stop (quiet task flow, section 7).

A bot's run that fails within FAST seconds of starting is not the same as one that fails after real work: it
changed nothing and will fail the same way again. Each such failure makes the bot wait before its next run, per bot,
15 s doubling to 8 h and reset by a run that completes normally. A person's message sent after the last failure does not wait it out:
it is the retry someone is watching, and its failure counts like any other (re-queued, it then waits as the rest do). After HOLD_AFTER of them in a row the bot is held:
no claim starts its queued work (a person's chat message included) until a person releases it, and the bot's
operator gets one Needs-you item while Health names the error class. The held bot's queued work is kept.

A hold does not need a person to end it. Tico tries one run on its own, the first PROBE_FIRST after the hold and each
later one after twice the gap before it (up to MAX_WAIT), so a shared outage (a provider answering every bot with an
error, the network down) lets every bot go again once it is over, while a broken bot still makes only a few runs a day.
The claim hands out exactly one job when the try is due and pushes the next try out at once (`probe`), so several
lanes or computers, or a runner that vanishes, never open the gate twice. The try is whichever queued job the queue
puts first, a person's chat message ahead of the rest. A run that completes normally releases the bot (`reset`), and a
fast failure of the try doubles the gap; a person can still release the bot at once.

A failure that is a provider's usage limit takes the usage-limit hold instead (backend/login_holds.py), however the
provider wrapped it: `classify` reads the text of any failed run, so a runner that did not flag it (an older one)
or an error that nests the limit inside another ("Prompt is too long - automatic compaction failed: You've hit
your weekly limit - resets Oct 26, 8pm (UTC)") still waits for the reset the text names. A failure that says the
session's prompt grew too large to send ("too many total text bytes", "Prompt is too long") makes the next run on that
thread compact its session first (`compact_first` in the config its claim hands out, once). Only a run that already
had `compact_first` and failed the same way again (an older runner that ignores it, or a compaction that did not help)
starts the next run in a fresh provider session, as a person's Clear does, with the reason `prompt_too_long_after_compact`.

What does not count: a run a person stopped, a usage limit or a model at capacity (their own waits), a run that
yielded or was handed back, and a run that failed after FAST seconds of work. Only a completed run resets the count.
"""

import json
import re

from clients import gateway

from . import login_holds
from .store import H

FAST = 60                 # a run that fails sooner than this after starting is a fast failure
FIRST_WAIT = 15           # seconds the bot waits after its first fast failure; doubles for each one in a row
MAX_WAIT = 8 * 3600       # and never longer than this
HOLD_AFTER = 5            # fast failures in a row that hold the bot
PROBE_FIRST = 30 * 60     # seconds a held bot waits before its first try; doubles for each failed try, up to MAX_WAIT
ERROR_CHARS = 300

SCHEMA = (
    "CREATE TABLE IF NOT EXISTS run_backoff(bot TEXT PRIMARY KEY, fails INTEGER NOT NULL DEFAULT 0, wait_until TEXT, "
    "error_class TEXT, error TEXT, last_failed_at TEXT, held_at TEXT, task_id TEXT, released_at TEXT, released_by TEXT)",)

# A provider's limit named anywhere in an error: "You've hit your weekly limit", "usage limit reached", "rate limit".
LIMIT_RE = re.compile(r"hit your (?:[a-z-]+ ){0,2}limit|\b(?:weekly|usage|session|monthly|spend(?:ing)?|rate|\d+-hour) "
                      r"limit|limit reached|usage_limit_reached|rate_limit_error", re.I)
# The session's prompt is too large to send; the same session fails the same way next time.
TOO_LONG_RE = re.compile(r"too many total text bytes|prompt is too long", re.I)
SIGN_IN_RE = re.compile(r"sign.?in|log.?in|unauthorized|authentication|invalid (?:x-)?api.?key|\b401\b", re.I)

USAGE_LIMIT, PROMPT_TOO_LONG, SIGN_IN, GATEWAY, OTHER = "usage_limit", "prompt_too_long", "sign_in", "gateway", "other"
CLASS_WORDS = {
    USAGE_LIMIT: "a provider usage limit",
    PROMPT_TOO_LONG: "the session grew too large to send",
    SIGN_IN: "the AI sign-in was refused",
    GATEWAY: "the Tico AI Gateway refused the run",
    OTHER: "an error before any work",
}


def ensure(c):
    for statement in SCHEMA:
        c.execute(statement)


def is_limit(text, now=None):
    """Whether an error's text says the provider's usage limit was hit, however the rest of it is worded. A model at
    capacity or a Tico Gateway code is not (login_holds.capacity, clients/gateway.py): they have their own waits."""
    text = str(text or "")
    if not text or gateway.run_code(text) or login_holds.capacity(text):
        return False
    return bool(LIMIT_RE.search(text) or login_holds.reset_from_text(text, now or H.now()))


def error_class(text):
    """What kind of failure an error text shows: one of USAGE_LIMIT, PROMPT_TOO_LONG, SIGN_IN, GATEWAY, OTHER."""
    text = str(text or "")
    if is_limit(text):
        return USAGE_LIMIT
    if TOO_LONG_RE.search(text):
        return PROMPT_TOO_LONG
    if gateway.run_code(text):
        return GATEWAY
    return SIGN_IN if SIGN_IN_RE.search(text) else OTHER


SUMMARY_CHARS = 600       # a failed run's summary is read as an error only when it is this short, never a long reply


def texts(c, aid, body):
    """What a failed run said went wrong: the runtime's error events, newest first, then its summary when it is short
    enough to be the error itself (a bot's own long text that mentions a limit is not one)."""
    from .execution import limit_errors
    summary = body.text if len(body.text or "") <= SUMMARY_CHARS else ""
    return [t for t in (*limit_errors(c, aid), summary) if t]


def classify(c, aid, body, now=None):
    """Read a failed or interrupted run's text and fix what an older runner did not say. A provider limit anywhere in
    it sets `body.limited` and, when the text names one, `limit_resets_at`, so the run takes the usage-limit hold
    (login_holds.after_turn) until that reset; the error events that show it are marked as limit notices, as a
    current runner marks them. Answers the kind of failure: USAGE_LIMIT, PROMPT_TOO_LONG or None."""
    now = now or H.now()
    if body.limited:
        return USAGE_LIMIT if body.limited else None   # the runner already read its limits (and any fallback's) itself
    candidates = texts(c, aid, body)
    if body.fallback:
        rows = []
        for event in c.execute("SELECT kind,payload FROM attempt_events WHERE attempt_id=? ORDER BY seq", (aid,)):
            payload = json.loads(event['payload'])
            text = payload.get('error') or payload.get('text') or ''
            if event['kind'] == 'diagnostic' and login_holds.HOP_RE.search(text):
                rows = []
            elif event['kind'] == 'error':
                rows.append(text)
        candidates = [*rows, body.text if len(body.text or '') <= SUMMARY_CHARS else '']
    elif body.primary_limited:
        return None
    found = [t for t in candidates if is_limit(t, now)]
    if found:
        body.limited = True
        if not body.limit_resets_at:
            for text in found:
                reset = login_holds.believable(login_holds.reset_from_text(text, now), now)
                if reset:
                    body.limit_resets_at = login_holds._iso(reset)
                    break
        for event in c.execute("SELECT id,payload FROM attempt_events WHERE attempt_id=? AND kind='error'",
                               (aid,)).fetchall():
            try:
                payload = json.loads(event["payload"])
            except ValueError:
                continue
            if isinstance(payload, dict) and not payload.get("limit") and is_limit(payload.get("error"), now):
                c.execute("UPDATE attempt_events SET payload=? WHERE id=?",
                          (json.dumps({**payload, "limit": True}), event["id"]))
        return USAGE_LIMIT
    if any(TOO_LONG_RE.search(t) for t in texts(c, aid, body)):
        return PROMPT_TOO_LONG
    return None


AFTER_COMPACT = "prompt_too_long_after_compact"


def fresh_session(c, settings, bot, conversation_id, aid, task_id=None):
    """The bot's next run in this conversation starts a new provider session (views.clear_chat_session, the same
    mechanism as a person's Clear): its history is reloaded from Tico instead of resuming the session that no longer
    fits. The failed run says so, on itself and on its task."""
    from .views import clear_chat_session
    clear_chat_session(c, settings, H.KEEPER, bot, conversation_id,
                       {"reason": AFTER_COMPACT, "attempt": aid, "task": task_id, "bot": bot})
    c.execute("UPDATE turns SET session_kind='new', session_why=? WHERE id=?", (AFTER_COMPACT, aid))


def too_long(c, settings, row, conversation_id, aid):
    """A run failed because its session's prompt was too large to send. A thread's first such failure marks it, and its
    next claim carries `compact_first` once (`take_compact`): the runner compacts the session before it runs. A run
    that already carried it and failed the same way again has a session compaction cannot save, so it starts anew."""
    bot = row["bot_id"]
    if c.execute("SELECT 1 FROM compact_first WHERE bot=? AND conversation_id=? AND taken_by=?",
                 (bot, conversation_id, aid)).fetchone():
        c.execute("DELETE FROM compact_first WHERE bot=? AND conversation_id=?", (bot, conversation_id))
        turn = c.execute("SELECT task_id FROM turns WHERE id=?", (aid,)).fetchone()
        fresh_session(c, settings, bot, conversation_id, aid, turn[0] if turn else None)
        return
    c.execute("INSERT INTO compact_first(bot,conversation_id,marked_at,taken_by) VALUES(?,?,?,NULL) "
              "ON CONFLICT(bot,conversation_id) DO UPDATE SET marked_at=excluded.marked_at,taken_by=NULL",
              (bot, conversation_id, H.now()))
    H.event(c, H.KEEPER, "session.compact_first", conversation_id, {"bot": bot, "attempt": aid})


def take_compact(c, bot, conversation_id, aid):
    """Whether the claim of run `aid` carries `compact_first`: the thread is marked, and no run that took the mark is
    still out. The mark is spent by the first claim that takes it; a run that ends without finishing (`compact_settle`)
    or whose lease lapsed gives it back."""
    return c.execute(
        "UPDATE compact_first SET taken_by=? WHERE bot=? AND conversation_id=? AND (taken_by IS NULL OR taken_by NOT IN "
        "(SELECT id FROM attempts WHERE state IN ('leased','running')))", (aid, bot, conversation_id)).rowcount > 0


def compact_settle(c, aid, completed):
    """The run that took a mark has ended: a completed run compacted and ran, so the mark is gone; any other end that
    is not a prompt too large to send gives it back for the next claim."""
    if completed:
        c.execute("DELETE FROM compact_first WHERE taken_by=?", (aid,))
    else:
        c.execute("UPDATE compact_first SET taken_by=NULL WHERE taken_by=?", (aid,))


# ---------------------------------------------------------------------------------------------------- the state
GET_SQL = "SELECT * FROM run_backoff WHERE bot=?"


def get(c, bot):
    """The bot's row, read once per snapshot (read_cache): a status page primes every bot's (events.prime_status_lines)."""
    from .read_cache import one
    row = one(c, GET_SQL, (bot,))
    return dict(row) if row else {}


def wait_for(fails):
    """Seconds the bot waits after `fails` fast failures in a row."""
    return min(FIRST_WAIT * 2 ** max(fails - 1, 0), MAX_WAIT)


def refusal(c, bot):
    """Why a claim passes over all of the bot's work for its failed runs: "run_hold" while it is held (until its next
    try is due), "backoff" until its wait ends, else None. Read only, as every refusal (execution.claim_refusal)."""
    row = get(c, bot)
    if not row:
        return None
    if row["held_at"]:
        return None if probe_due(c, bot, row) else "run_hold"
    return "backoff" if row["wait_until"] and row["wait_until"] > H.now() else None


def probe_wait(fails):
    """Seconds a held bot waits for its next try once `fails` fast failures in a row are on record."""
    return min(PROBE_FIRST * 2 ** max(fails - HOLD_AFTER, 0), MAX_WAIT)


def probe_at(state):
    """When a held bot's next try is due: the time kept in `wait_until`, else PROBE_FIRST after the hold began (a hold
    from before tries existed has none). None for a bot that is not held."""
    return (state.get("wait_until") or H.shift(state["held_at"], seconds=PROBE_FIRST)) if state.get("held_at") else None


def probe_due(c, bot, state, now=None):
    """Whether a held bot may take a try now: its time has come and none of its runs is out."""
    at = probe_at(state)
    return bool(at and at <= (now or H.now()) and not c.execute(
        "SELECT 1 FROM attempts WHERE bot_id=? AND state IN ('leased','running') LIMIT 1", (bot,)).fetchone())


def probe(c, bot):
    """Called by the claim, in its write transaction, with the bot whose job it is about to hand out: True when the job
    must be passed over because the bot is held and its try is not due. When it is due this is the try: the next one is
    pushed out as if it failed, so a second claim in the same window, a second lane or computer, and a try whose runner
    vanishes all wait the longer gap."""
    state = get(c, bot)
    if not state.get("held_at"):
        return False
    if not probe_due(c, bot, state):
        return True
    c.execute("UPDATE run_backoff SET wait_until=? WHERE bot=?",
              (H.shift(H.now(), seconds=probe_wait(int(state.get("fails") or 0) + 1)), bot))
    return False


def _since_failure(c, bot):
    return get(c, bot).get("last_failed_at") or ""


def person_waiting(c, bot):
    """Whether a person wrote to the bot since its last failed run: the job a back-off wait lets through."""
    return bool(c.execute("SELECT 1 FROM jobs j JOIN messages m ON m.id=j.message_id WHERE j.bot_id=? AND j.state='queued' "
                          "AND m.sender_id LIKE 'human:%' AND m.created_at>? LIMIT 1", (bot, _since_failure(c, bot))).fetchone())


def job_refusal(c, bot, message):
    """"backoff" for a job the bot's wait holds: any but a person's message sent since the last failure. Read only
    (execution.job_refusal)."""
    if refusal(c, bot) != "backoff":
        return None
    fresh = str(message.get("sender_id") or "").startswith("human:") and str(message.get("created_at") or "") > _since_failure(c, bot)
    return None if fresh else "backoff"


def reset(c, bot):
    """A run completed normally: the count starts over. A held bot is released by it: the try worked, or the cause
    went away by itself. The operator's Needs-you item closes and an event says so."""
    state = get(c, bot)
    if state.get("held_at"):
        from . import bot_stops
        c.execute("UPDATE run_backoff SET fails=0,wait_until=NULL,held_at=NULL,task_id=NULL WHERE bot=?", (bot,))
        bot_stops.close_item(c, state.get("task_id"),
                             f"{bot_stops.name_of(c, bot)} ran normally again, so Tico released it.")
        H.event(c, H.KEEPER, "bot.run_recovered", bot, {"fails": state.get("fails"), "error_class": state.get("error_class")})
        return
    c.execute("UPDATE run_backoff SET fails=0,wait_until=NULL WHERE bot=? AND held_at IS NULL AND fails>0", (bot,))


def fast(row, now):
    """Whether the run failed within FAST seconds of starting (its claim, for a run that never reported a start)."""
    began, at = H.parse_ts(row["started_at"] or row["created_at"]), H.parse_ts(now)
    return began is not None and at is not None and (at - began).total_seconds() < FAST


def failed(c, settings, row, body, aid, now=None):
    """Count a run that ended in failure and wait or hold. The caller leaves out the runs that do not count (a Stop, a
    usage limit, a busy model, a yield). A slow failure changes nothing. Answers the bot's new state, or None."""
    now = now or H.now()
    if not fast(row, now):
        return None
    bot = row["bot_id"]
    text = (texts(c, aid, body) or [""])[0]
    kind = error_class(text)
    error = " ".join(text.split())[:ERROR_CHARS]
    state = get(c, bot)
    fails = int(state.get("fails") or 0) + 1
    held = bool(state.get("held_at"))
    hold_now = fails >= HOLD_AFTER and not held
    if fails < HOLD_AFTER:
        wait = H.shift(now, seconds=wait_for(fails))
    elif held and str(row["created_at"]) >= str(state["held_at"]):
        wait = H.shift(now, seconds=probe_wait(fails))      # a try failed: the next one comes later
    else:
        wait = state.get("wait_until")                      # held now (hold sets it), or a run out before the hold
    c.execute("INSERT INTO run_backoff(bot,fails,wait_until,error_class,error,last_failed_at) VALUES(?,?,?,?,?,?) "
              "ON CONFLICT(bot) DO UPDATE SET fails=excluded.fails,wait_until=excluded.wait_until,"
              "error_class=excluded.error_class,error=excluded.error,last_failed_at=excluded.last_failed_at",
              (bot, fails, wait, kind, error, now))
    if hold_now:
        hold(c, settings, bot, fails, kind, error, now)
    return get(c, bot)


def hold(c, settings, bot, fails, kind, error, now):
    """Hold the bot: one Needs-you item for its operator, and an event. Its queued work is kept, and its first try is
    due PROBE_FIRST from now."""
    from . import reconnects
    # A sign-in a standing login card on its task already puts in front of the person who fixes it is that card, not
    # a second item (backend/reconnects.py): the hold still stands, Health names it, and the card's fix releases it.
    task_id = None if kind == SIGN_IN and reconnects.bot_open(c, bot, "login:") else _item(c, settings, bot, fails, kind, error)
    c.execute("UPDATE run_backoff SET held_at=?,task_id=?,wait_until=? WHERE bot=?",
              (now, task_id, H.shift(now, seconds=PROBE_FIRST), bot))
    H.event(c, H.KEEPER, "bot.run_held", bot, {"fails": fails, "error_class": kind, "task": task_id})
    H.hook(c, "bot.held", bot, lambda: {"bot": bot, "reason": "failed_runs", "fails": fails, "error_class": kind, "task": task_id},
           H.KEEPER, revision=now)


def _item(c, settings, bot, fails, kind, error):
    from . import bot_stops
    name = bot_stops.name_of(c, bot)
    return bot_stops.raise_item(
        c, settings, bot, f"{name} stopped after {fails} failed runs in a row",
        f"Tico stopped {name}: its last {fails} runs each failed within a minute, with {CLASS_WORDS[kind]}.\n\n"
        f"Last error: {error or 'none was reported'}\n\n"
        f"Tico tries one run of {name} on its own, first in {PROBE_FIRST // 60} minutes and then at growing intervals, and "
        f"releases it when one completes. To start it sooner, check its last runs in Runs, fix the cause and release "
        f"{name} (Settings > Bots > {name}, or `hub bot release {bot}`). Everything queued for it is kept.")


def raise_hold_item(c, settings, bot):
    """A held bot whose login card stood for its item (backend/reconnects.py) lost that card unfixed: its own item."""
    state = get(c, bot)
    if state.get("held_at") and not state.get("task_id"):
        task_id = _item(c, settings, bot, int(state.get("fails") or 0), state.get("error_class") or OTHER, state.get("error"))
        c.execute("UPDATE run_backoff SET task_id=? WHERE bot=?", (task_id, bot))
        return task_id
    return None


def release(c, actor, bot):
    """Lift the hold and the wait, and start the count over. Answers whether the bot had either."""
    state = get(c, bot)
    if not (state.get("held_at") or state.get("wait_until") or state.get("fails")):
        return False
    from . import bot_stops
    c.execute("UPDATE run_backoff SET fails=0,wait_until=NULL,held_at=NULL,task_id=NULL,released_at=?,released_by=? "
              "WHERE bot=?", (H.now(), actor, bot))
    bot_stops.close_item(c, state.get("task_id"), "Released.")
    if state.get("held_at"):
        H.event(c, actor, "bot.run_released", bot, {"fails": state.get("fails"), "error_class": state.get("error_class")})
        H.hook(c, "bot.released", bot, lambda: {"bot": bot, "reason": "failed_runs", "released_by": actor}, actor,
               revision=H.now())
    return bool(state.get("held_at") or state.get("wait_until"))


def held(c):
    """Every bot held for failed runs: the rows Health lists."""
    return [dict(r) for r in c.execute("SELECT * FROM run_backoff WHERE held_at IS NOT NULL ORDER BY held_at")]


def words(c, bot):
    """What the pages say about the bot's failed runs: the hold, or the wait before its next try."""
    from . import bot_stops
    state = get(c, bot)
    name = bot_stops.name_of(c, bot)
    if state.get("held_at"):
        cause = CLASS_WORDS.get(state.get("error_class") or "") or CLASS_WORDS[OTHER]
        return (f"Tico stopped {name}: its last {state.get('fails')} runs failed within a minute ({cause}). "
                "Tico tries one run on its own at growing intervals and releases it when one completes. "
                "Check its last runs, or release it now.")
    until, at = H.parse_ts(state.get("wait_until")), H.parse_ts(H.now())
    left = max(1, int((until - at).total_seconds())) if until is not None and at is not None else 0
    return f"{name} is pausing {left} s after a failed run before it tries again" if left else ""
