"""Failed runs wait, then stop (quiet task flow, section 7).

A bot's run that fails within FAST seconds of starting is not the same as one that fails after real work: it changed
nothing and will fail the same way again. Each such failure makes the bot wait before its next run, and HOLD_AFTER in a
row hold it: a `failing` hold on the bot (backend/holds.py, whose KINDS row sets the waits and tries). A run that
completes normally starts the count over. A person's message sent after the last failure does not wait it out: it is
the retry someone is watching, and its failure counts like any other (re-queued, it then waits as the rest do). A held
bot starts none of its queued work (a person's chat message included) until a person releases it or a try completes;
the bot's operator gets one Needs-you item while Health names the error class. Its queued work is kept.

Tico tries one run of a held bot on its own (holds.take_probe), at growing intervals, so a shared outage (a provider
answering every bot with an error, the network down) lets every bot go again once it is over, while a broken bot still
makes only a few runs a day. The try is whichever queued job the queue puts first, a person's chat message ahead of
the rest. A run that completes normally releases the bot (`reset`), and a fast failure of the try doubles the gap; a
person can still release the bot at once.

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

from . import holds, login_holds
from .store import H

FAST = 60  # a run that fails sooner than this after starting is a fast failure
FAILING = holds.FAILING
HOLD_AFTER = holds.KINDS[FAILING].held_after  # fast failures in a row that hold the bot
ERROR_CHARS = 300

# A provider's limit named anywhere in an error: "You've hit your weekly limit", "usage limit reached", "rate limit".
LIMIT_RE = re.compile(
    r"hit your (?:[a-z-]+ ){0,2}limit|\b(?:weekly|usage|session|monthly|spend(?:ing)?|rate|\d+-hour) "
    r"limit|limit reached|usage_limit_reached|rate_limit_error",
    re.I,
)
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


def is_limit(text, now=None):
    """Whether an error's text says the provider's usage limit was hit, however the rest of it is worded, or that it
    refused the plan for its billing (login_holds.payment): both are the login's, held there for every bot on it. A
    model at capacity or a Tico Gateway code is not (login_holds.capacity, clients/gateway.py): they have their own
    waits."""
    text = str(text or "")
    if not text or gateway.run_code(text) or login_holds.capacity(text):
        return False
    return bool(LIMIT_RE.search(text) or login_holds.payment(text) or login_holds.reset_from_text(text, now or H.now()))


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


SUMMARY_CHARS = 600  # a failed run's summary is read as an error only when it is this short, never a long reply


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
        return USAGE_LIMIT if body.limited else None  # the runner already read its limits (and any fallback's) itself
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
        for event in c.execute(
            "SELECT id,payload FROM attempt_events WHERE attempt_id=? AND kind='error'", (aid,)
        ).fetchall():
            try:
                payload = json.loads(event["payload"])
            except ValueError:
                continue
            if isinstance(payload, dict) and not payload.get("limit") and is_limit(payload.get("error"), now):
                c.execute(
                    "UPDATE attempt_events SET payload=? WHERE id=?",
                    (json.dumps({**payload, "limit": True}), event["id"]),
                )
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

    clear_chat_session(
        c,
        settings,
        H.TICO_ACTOR,
        bot,
        conversation_id,
        {"reason": AFTER_COMPACT, "attempt": aid, "task": task_id, "bot": bot},
    )
    c.execute("UPDATE attempts SET session_kind='new', session_why=? WHERE id=?", (AFTER_COMPACT, aid))


def too_long(c, settings, row, conversation_id, aid):
    """A run failed because its session's prompt was too large to send. A thread's first such failure marks it, and its
    next claim carries `compact_first` once (`take_compact`): the runner compacts the session before it runs. A run
    that already carried it and failed the same way again has a session compaction cannot save, so it starts anew."""
    bot = row["bot_id"]
    if c.execute(
        "SELECT 1 FROM compact_first WHERE bot=? AND conversation_id=? AND taken_by=?", (bot, conversation_id, aid)
    ).fetchone():
        c.execute("DELETE FROM compact_first WHERE bot=? AND conversation_id=?", (bot, conversation_id))
        turn = c.execute("SELECT task_id FROM attempts WHERE id=?", (aid,)).fetchone()
        fresh_session(c, settings, bot, conversation_id, aid, turn[0] if turn else None)
        return
    c.execute(
        "INSERT INTO compact_first(bot,conversation_id,marked_at,taken_by) VALUES(?,?,?,NULL) "
        "ON CONFLICT(bot,conversation_id) DO UPDATE SET marked_at=excluded.marked_at,taken_by=NULL",
        (bot, conversation_id, H.now()),
    )
    H.event(c, H.TICO_ACTOR, "session.compact_first", conversation_id, {"bot": bot, "attempt": aid})


def take_compact(c, bot, conversation_id, aid):
    """Whether the claim of run `aid` carries `compact_first`: the thread is marked, and no run that took the mark is
    still out. The mark is spent by the first claim that takes it; a run that ends without finishing (`compact_settle`)
    or whose lease lapsed gives it back."""
    return (
        c.execute(
            "UPDATE compact_first SET taken_by=? WHERE bot=? AND conversation_id=? AND (taken_by IS NULL OR taken_by NOT IN "
            "(SELECT id FROM attempts WHERE state IN ('leased','running')))",
            (aid, bot, conversation_id),
        ).rowcount
        > 0
    )


def compact_settle(c, aid, completed):
    """The run that took a mark has ended: a completed run compacted and ran, so the mark is gone; any other end that
    is not a prompt too large to send gives it back for the next claim."""
    if completed:
        c.execute("DELETE FROM compact_first WHERE taken_by=?", (aid,))
    else:
        c.execute("UPDATE compact_first SET taken_by=NULL WHERE taken_by=?", (aid,))


# ---------------------------------------------------------------------------------------------------- the hold
# A `failing` hold on the bot (backend/holds.py) is open while fast failures in a row are on record (its streak). Its
# until_at is the wait after the last one; a held bot has none, and probe_at is when its next try is due. Its details
# keep the last failure's class, its error and when it failed, and when the bot was held.


def _read(row):
    """A hold row as a dict, its details read ({} for none)."""
    if not row:
        return {}
    try:
        details = json.loads(row["details"] or "{}")
    except (TypeError, ValueError):
        details = {}
    return {**dict(row), "details": details if isinstance(details, dict) else {}}


def get(c, bot, loads=None):
    """The bot's open hold ({} when none): from what the claim read for its bots (claim_loads) when it is given, else
    once per snapshot (a page primes every bot's: holds.prime)."""
    return _read(
        loads.failing[bot] if loads is not None and bot in loads.failing else holds.get(c, FAILING, "bot", bot)
    )


def is_held(hold):
    """Whether the hold holds the bot (HOLD_AFTER failures in a row), not only a wait."""
    return bool(hold) and hold["until_at"] is None


def probe(c, bot, job_id):
    """Called by the claim, in its write transaction, with the job it is about to hand out: True when it must pass the
    job over because the bot is held and this is not its try (holds.take_probe)."""
    hold = get(c, bot)
    return is_held(hold) and not holds.take_probe(c, hold, job_id)


def _since_failure(c, bot):
    return get(c, bot).get("details", {}).get("last_failed_at") or ""


def person_waiting(c, bot):
    """Whether a person wrote to the bot since its last failed run: the job a back-off wait lets through."""
    return bool(
        c.execute(
            "SELECT 1 FROM jobs j JOIN messages m ON m.id=j.message_id WHERE j.bot_id=? AND j.state='queued' "
            f"AND {H.typed_by_person_sql()} AND m.created_at>? LIMIT 1",
            (bot, _since_failure(c, bot)),
        ).fetchone()
    )


def typed_since(c, bot, message):
    """Whether a person typed this message after the bot's last failed run: the one job a back-off wait lets through."""
    return H.typed_by_person(message) and str(message.get("created_at") or "") > _since_failure(c, bot)


def _put(c, bot, hold, **change):
    """Write the bot's hold: what it holds now, with `change` over it."""
    kept = {k: hold.get(k) for k in ("reason", "details", "streak", "until_at", "probe_at", "upkeep_task_id")}
    return holds.put(c, FAILING, "bot", bot, actor=H.TICO_ACTOR, **{**kept, "reason": kept["reason"] or "", **change})


def reset(c, bot):
    """A run completed normally: the count starts over. A held bot is released by it: the try worked, or the cause
    went away by itself. The operator's Needs-you item closes and an event says so."""
    hold = get(c, bot)
    if not hold:
        return
    held = is_held(hold)
    holds.release(c, FAILING, "bot", bot, actor=H.TICO_ACTOR, how="probe_passed" if held else "expired")
    if held:
        from . import bot_stops

        bot_stops.close_item(
            c, hold["upkeep_task_id"], f"{bot_stops.name_of(c, bot)} ran normally again, so Tico released it."
        )
        H.event(
            c,
            H.TICO_ACTOR,
            "bot.run_recovered",
            bot,
            {"fails": hold["streak"], "error_class": hold["details"].get("error_class")},
        )


def fast(row, now):
    """Whether the run failed within FAST seconds of starting (its claim, for a run that never reported a start)."""
    began, at = H.parse_ts(row["started_at"] or row["created_at"]), H.parse_ts(now)
    return began is not None and at is not None and (at - began).total_seconds() < FAST


def failed(c, settings, row, body, aid, now=None):
    """Count a run that ended in failure and wait or hold. The caller leaves out the runs that do not count (a Stop, a
    usage limit, a busy model, a yield). A slow failure changes nothing."""
    now = now or H.now()
    if not fast(row, now):
        return
    bot = row["bot_id"]
    text = (texts(c, aid, body) or [""])[0]
    kind = error_class(text)
    error = " ".join(text.split())[:ERROR_CHARS]
    hold = get(c, bot)
    fails = int(hold.get("streak") or 0) + 1
    held_at, task_id = hold["details"].get("held_at") if is_held(hold) else None, hold.get("upkeep_task_id")
    later = H.shift(now, seconds=holds.wait(FAILING, fails))
    until, probe_at, newly = later, None, False
    if held_at:
        # Held already: a try that failed puts the next one later; a run out before the hold leaves it as it is.
        until, probe_at = None, later if str(row["created_at"]) >= held_at else hold["probe_at"]
    elif fails >= HOLD_AFTER:
        held_at, until, probe_at, newly = now, None, later, True
        task_id = _hold_item(c, settings, bot, fails, kind, error)
    details = {"error_class": kind, "error": error, "last_failed_at": now, "held_at": held_at}
    reason = _sentence(c, bot, fails, kind) if held_at else ""
    _put(
        c,
        bot,
        hold,
        streak=fails,
        until_at=until,
        probe_at=probe_at,
        details=details,
        reason=reason,
        upkeep_task_id=task_id,
    )
    if newly:
        H.event(c, H.TICO_ACTOR, "bot.run_held", bot, {"fails": fails, "error_class": kind, "task": task_id})
        H.hook(
            c,
            "bot.held",
            bot,
            lambda: {"bot": bot, "reason": "failed_runs", "fails": fails, "error_class": kind, "task": task_id},
            H.TICO_ACTOR,
            revision=now,
        )


def _hold_item(c, settings, bot, fails, kind, error):
    """The held bot's one Needs-you item for its operator, or None. A sign-in a standing login card on its task already
    puts in front of the person who fixes it is that card, not a second item (backend/reconnects.py): the hold still
    stands, Health names it, and the card's fix releases it."""
    from . import reconnects

    return (
        None
        if kind == SIGN_IN and reconnects.bot_open(c, bot, "login:")
        else _item(c, settings, bot, fails, kind, error)
    )


def _item(c, settings, bot, fails, kind, error):
    from . import bot_stops

    name = bot_stops.name_of(c, bot)
    first = holds.KINDS[FAILING].probe_first
    return bot_stops.raise_item(
        c,
        settings,
        bot,
        f"{name} stopped after {fails} failed runs in a row",
        f"Tico stopped {name}: its last {fails} runs each failed within a minute, with {CLASS_WORDS[kind]}.\n\n"
        f"Last error: {error or 'none was reported'}\n\n"
        f"Tico tries one run of {name} on its own, first in {first // 60} minutes and then at growing intervals, and "
        f"releases it when one completes. To start it sooner, check its last runs in Runs, fix the cause and release "
        f"{name} (Settings > Bots > {name}, or `tico bot release {bot}`). Everything queued for it is kept.",
    )


def raise_hold_item(c, settings, bot):
    """A held bot whose login card stood for its item (backend/reconnects.py) lost that card unfixed: its own item."""
    hold = get(c, bot)
    if is_held(hold) and not hold["upkeep_task_id"]:
        details = hold["details"]
        task_id = _item(c, settings, bot, hold["streak"], details.get("error_class") or OTHER, details.get("error"))
        _put(c, bot, hold, upkeep_task_id=task_id)
        return task_id
    return None


def fold_item(c, bot):
    """A login card on the held bot's task asks for the same fix as its item: the item closes, the card stands for it."""
    hold = get(c, bot)
    if is_held(hold) and hold["details"].get("error_class") == SIGN_IN and hold["upkeep_task_id"]:
        from . import bot_stops

        _put(c, bot, hold, upkeep_task_id=None)
        bot_stops.close_item(c, hold["upkeep_task_id"], "A reconnect card on its task asks for the same fix.")


def release(c, actor, bot):
    """Lift the hold or the wait, and start the count over. Answers whether the bot had either."""
    ended = holds.release(
        c, FAILING, "bot", bot, actor=actor, how="botops" if str(actor).startswith("bot:") else "person"
    )
    if not ended:
        return False
    from . import bot_stops

    bot_stops.close_item(c, ended["upkeep_task_id"], "Released.")
    if ended["until_at"] is None:
        fails, kind = ended["streak"], _read(ended)["details"].get("error_class")
        H.event(c, actor, "bot.run_released", bot, {"fails": fails, "error_class": kind})
        H.hook(
            c,
            "bot.released",
            bot,
            lambda: {"bot": bot, "reason": "failed_runs", "released_by": actor},
            actor,
            revision=H.now(),
        )
    return True


HELD_SQL = (
    "SELECT * FROM holds WHERE kind='failing' AND holdable_type='bot' AND until_at IS NULL AND released_at IS NULL"
)


def held(c):
    """Every bot held for failed runs, longest held first: the holds Health lists."""
    return [_read(row) for row in c.execute(HELD_SQL + " ORDER BY json_extract(details,'$.held_at')")]


def _sentence(c, bot, fails, kind):
    from . import bot_stops

    return (
        f"Tico stopped {bot_stops.name_of(c, bot)}: its last {fails} runs failed within a minute "
        f"({CLASS_WORDS.get(kind) or CLASS_WORDS[OTHER]}). Tico tries one run on its own at growing intervals and "
        "releases it when one completes. Check its last runs, or release it now."
    )


def words(c, bot):
    """What the pages say about the bot's failed runs: the hold, or the wait before its next try."""
    hold = get(c, bot)
    if is_held(hold):
        return hold["reason"]
    from . import bot_stops

    until, at = H.parse_ts(hold.get("until_at")), H.parse_ts(H.now())
    left = max(1, int((until - at).total_seconds())) if until is not None and at is not None else 0
    return f"{bot_stops.name_of(c, bot)} is pausing {left} s after a failed run before it tries again" if left else ""
