"""Topic changed: before a person's message in their direct chat with a bot is sent, a Decide check of whether it starts
a new topic (`questions/topic-change.json`, through the server's Decide engine, `backend/judge.py` `server_engine`).
On a confident new topic the session is cleared exactly as the person's own Clear does (`views.clear_chat_session`)
and the message carries `refs.topic_changed`, which the chat shows as a quiet "Topic changed" line above it.

The gate is plain code, read from what runners already report. The session is the bot's provider thread the message
would continue (its `bot_sessions` row); its last turn is the newest finished turn on that thread.
- Warm: that turn finished within its harness's prompt-cache lifetime (CACHE_LIFETIME, WARM_SECONDS when the harness
  is not listed) and read at least WARM_SHARE of its input from the cache (`cached_tokens` over `input_tokens +
  cached_tokens`). Anything else is cold: no turn, an older one, a small cache hit, or a harness that reports no cache
  use (Grok, today).
- Context size: that turn's newest request of the bot's own (not a subagent's) whole prompt (`turns.prompt_tokens`,
  uncached + cached), when its runner marked its requests. The turn's summed `input_tokens + cached_tokens` count every
  request of the turn again and are not a context size; without `prompt_tokens` (an older runner, a harness that does
  not mark them) the size is unknown and never triggers a check.
- Check at BIG_CONTEXT tokens or more, or when the cache is cold; not under BIG_CONTEXT with a warm cache.
- Never when the team's `topic_fresh` rule is off, the chat is a shared bot's room (even one with a single person), the
  chat has an active goal, a turn is running, or the chat has fewer than MIN_TURNS turns since its last clear.
- Never for a send `considered` turns away: a command, a task chat's message, or one carrying files by any route.

The whole check, its reads, the call and the answer, runs on a worker the send waits for at most BUDGET seconds; an
answer later than that is dropped, whichever thread runs first. Its reads are one snapshot, admission to history. A decision holds only for the chat as it was read: `apply` re-reads it in the send's
own transaction and clears nothing when the rule, a goal, a running turn, the session's epoch or the chat's newest
message changed meanwhile.

Fail open: no decision model, rehearsal, the spend cap, the daily call budget, an error, no answer within BUDGET seconds,
`not_sure`, `same_topic` or a `new_topic` under the set's threshold all send the message in the same session with no
line. Each check is one `judge.call` row in the person's name, counted against their daily Decide calls as the Slack
gateway's routing is (label, model, tokens, latency, cost, the answer, the decision, the delay); never the chat's text.
It is written after the send stops waiting, so it never adds to the delay; the call is reserved before Decide is asked
and handed back once that row counts it, so sends at the same moment cannot pass the daily cap together. Each clear is
one `chat.topic_changed` event: the tokens before the clear and the delay added before the send; a decision `apply`
refused is one `chat.topic_kept` event saying why."""

import logging
import threading
import time

from clients import judge as J
from . import rooms, team_rules
from .judge import audit_detail, daily_cap, over_spend_cap, server_engine, used_today
from .store import H, encode

LOG = logging.getLogger("tico.topic_change")
_clock = time.monotonic
QUESTION_SET = "topic-change"
RULE = "topic_fresh"
# The most a send waits for the check, all of it: the read, the call and the answer.
BUDGET = 1.5
# The default prompt-cache lifetime: a session whose last turn read the cache this recently is warm.
WARM_SECONDS = 300
# Each harness's prompt-cache lifetime in seconds, by the runtime its sessions are kept under (`bot_sessions.runtime`;
# antigravity runs as gemini). A row is either a lifetime its provider documents (the shortest, when it gives a range:
# a cold reading only asks Decide, a warm one skips it) or, where no provider page gives one, the WARM_SECONDS fallback.
CACHE_LIFETIME = {
    # Documented: "By default, the cache has a 5-minute lifetime" (1 hour is opt-in).
    # https://platform.claude.com/docs/en/build-with-claude/prompt-caching
    "claude": 300,
    # Documented: in-memory entries "typically remain active for around 5 to 10 minutes of inactivity, up to one
    # hour"; extended retention is longer. https://developers.openai.com/api/docs/guides/prompt-caching
    "codex": 300,
    # Fallback: Google's caching page gives no lifetime for implicit caching
    # (https://ai.google.dev/gemini-api/docs/caching); OpenRouter's guide reports Gemini's implicit cache lives
    # "on average 3-5 minutes, but will vary".
    "gemini": WARM_SECONDS,
    # Fallback: xAI's models page prices cached input but gives no lifetime (https://docs.x.ai/docs/models). The value
    # does not matter today: the runner's Grok host reports no cached tokens, so its sessions always read cold.
    "grok": WARM_SECONDS,
    # Fallback: OpenRouter documents each upstream's own expiry, not one of its own (Anthropic 5 minutes by default,
    # Gemini explicit writes 5 minutes, several upstreams none).
    # https://openrouter.ai/docs/guides/best-practices/prompt-caching
    "pi": WARM_SECONDS,
    # Fallback: Cursor's docs price cache reads per model but give no lifetime (https://cursor.com/docs).
    "cursor": WARM_SECONDS,
    # Fallback: no lifetime found for Muse Code; Cursor's model table prices Muse Spark cached input with no lifetime
    # (https://cursor.com/docs).
    "musecode": WARM_SECONDS,
}
# The least share of the last turn's input read from the cache for the session to count as warm.
WARM_SHARE = 0.5
BIG_CONTEXT = 100_000
MIN_TURNS = 3
# The turns the check reads, and how much of each message it sends.
RECENT_TURNS = 3
TRIM = 1500


def _trim(text):
    text = " ".join(str(text or "").split())
    return text if len(text) <= TRIM else text[:TRIM - 1].rstrip() + "…"


def last_turn(c, settings, bot):
    """The newest finished turn on the provider thread the bot's direct chats continue, or None (no thread yet, or one
    just cleared)."""
    from .shared_bots import follow
    from . import providers
    config = c.execute("SELECT config FROM bot_config WHERE bot=?", (bot,)).fetchone()
    cfg = follow(c, bot, H._json(config[0], {}) or {}) if config else {}
    runtime, model = providers.bot_choice(c, settings, cfg)
    session = c.execute("SELECT thread_id FROM bot_sessions WHERE bot=? AND runtime=? AND model=?",
                        (bot, runtime, model)).fetchone()
    if not session or not session[0]:
        return None
    row = c.execute("SELECT finished_at,input_tokens,cached_tokens,prompt_tokens FROM turns WHERE bot=? AND thread_id=? "
                    "AND finished_at IS NOT NULL ORDER BY finished_at DESC LIMIT 1", (bot, session[0])).fetchone()
    return {**dict(row), "runtime": runtime} if row else None


def warmth(turn, now=None):
    """(warm, context tokens) of a session from its last turn (`last_turn`). The context is None when unknown."""
    if not turn:
        return False, None
    cached, fresh = int(turn.get("cached_tokens") or 0), turn.get("input_tokens")
    lifetime = CACHE_LIFETIME.get(turn.get("runtime"), WARM_SECONDS)
    context = turn.get("prompt_tokens")
    context = int(context) if context is not None else None
    finished, at = H.parse_ts(turn.get("finished_at")), H.parse_ts(now or H.now())
    age = (at - finished).total_seconds() if finished and at else None
    # An unknown fresh count (an older row, a runner that sent none) is cold: counting it as 0 would read 100% cached.
    share = cached / (cached + int(fresh)) if cached > 0 and fresh is not None else 0.0
    return share >= WARM_SHARE and age is not None and 0 <= age < lifetime, context


def epoch_since(c, conversation_id):
    """When the chat's session was last cleared, or None."""
    row = c.execute("SELECT updated_at FROM session_epochs WHERE conversation_id=?", (conversation_id,)).fetchone()
    return row[0] if row else None


def turns_since(c, bot, conversation_id, since):
    """The bot's finished turns on this chat since its last clear."""
    return c.execute("SELECT count(*) FROM turns t JOIN messages m ON m.id=t.message_id WHERE t.bot=? "
                     "AND m.conversation_id=? AND t.finished_at IS NOT NULL AND t.started_at>?",
                     (bot, conversation_id, since or "")).fetchone()[0]


def _held(c, conv):
    """Why a decision may not touch this chat now, or None: the rule is off, it is a shared bot's room, it has an
    active goal or a turn is running."""
    from .chat_goals import current
    from .views import chat_running
    if not team_rules.load(c)[RULE]:
        return "off"
    if conv.get("scope") == rooms.SHARED:
        return "shared"
    goal = current(c, conv["id"])
    if goal and goal.get("status") == "active":
        return "goal"
    if chat_running(c, conv["id"]):
        return "running"
    return None


def snapshot(c, conversation_id):
    """What a decision was made on: the session's epoch and the chat's newest message."""
    epoch = c.execute("SELECT epoch FROM session_epochs WHERE conversation_id=?", (conversation_id,)).fetchone()
    newest = c.execute("SELECT id FROM messages WHERE conversation_id=? ORDER BY created_at DESC, id DESC LIMIT 1",
                       (conversation_id,)).fetchone()
    return {"epoch": epoch[0] if epoch else 0, "newest": newest[0] if newest else None}


def gate(c, settings, bot, conv):
    """(check, reason, facts) for a message about to go into this direct chat. `reason` says why it is or is not
    checked; `facts` are the tokens and warmth the measures read."""
    conversation_id = conv["id"]
    held = _held(c, conv)
    if held:
        return False, held, {}
    if turns_since(c, bot, conversation_id, epoch_since(c, conversation_id)) < MIN_TURNS:
        return False, "few_turns", {}
    warm, context = warmth(last_turn(c, settings, bot))
    facts = {"warm": warm, "context_tokens": context}
    if context is not None and context >= BIG_CONTEXT:
        return True, "big", facts
    if not warm:
        return True, "cold", facts
    return False, "warm", facts


def _refs(message):
    refs = message.get("refs")
    if refs is None:
        refs = H._json(message.get("refs"), {})
    return refs if isinstance(refs, dict) else {}


def recent(c, who, bot, conversation_id):
    """The chat's last RECENT_TURNS turns since its last clear, oldest first: what the person said and what the bot
    answered, each trimmed. Only the person's and the bot's own words: task lines, goal actions and maintenance notes
    are left out, as is anything the person may not read."""
    from . import task_privacy as privacy
    rows = privacy.page(c, who, conversation_id, limit=60, since=epoch_since(c, conversation_id))["messages"]
    turns = []
    for message in rows:                      # oldest first
        refs = _refs(message)
        if (refs.get("goal_action") or refs.get("maintenance") or refs.get("task") or refs.get("task_id")
                or refs.get("task_started") or refs.get("task_report")):
            continue
        sender = message.get("sender_id")
        if sender == who.actor:
            turns.append({"person": _trim(message.get("body")), "bot": ""})
        elif sender == "bot:" + bot and turns:
            turns[-1]["bot"] = _trim((turns[-1]["bot"] + " " + str(message.get("body") or "")).strip())
    return turns[-RECENT_TURNS:]


_WORKERS = set()
# Each person's daily Decide calls asked but not yet written as `judge.call` rows (`_reserve`).
_SLOTS, _SLOTS_LOCK = {}, threading.Lock()


def _reserve(store, who):
    """Take one of the person's daily Decide calls before asking: True when one was free. A call in flight counts until
    its audit row is written (`_release`), so sends at the same moment cannot pass the cap together."""
    with _SLOTS_LOCK:
        with store.read() as c:
            used = used_today(c, who.actor)
        if used + _SLOTS.get(who.actor, 0) >= daily_cap(store.settings, who.role):
            return False
        _SLOTS[who.actor] = _SLOTS.get(who.actor, 0) + 1
        return True


def _release(who):
    """Hand back a reserved call: its audit row now counts it, or no call was made."""
    with _SLOTS_LOCK:
        left = _SLOTS.get(who.actor, 0) - 1
        if left > 0:
            _SLOTS[who.actor] = left
        else:
            _SLOTS.pop(who.actor, None)


def settle(timeout=5.0):
    """Wait for checks still finishing on their own threads (their late answer, their audit row). For tests."""
    for worker in list(_WORKERS):
        worker.join(timeout)


def considered(body, uploads):
    """Whether a person's send (`models.ChatCreate`) is one the check looks at: plain text, not a command or a task
    chat's message, and no files, whether referenced (`body.attachments`) or uploaded with it (`uploads`). `check` asks it
    first and every caller must say what it uploads, so no route that sends files can be checked."""
    return not (body.command or (body.refs or {}).get("task") or body.attachments or uploads)


def check(store, primary, who, bot, body, admit=None, *, uploads):
    """Whether to clear this person's direct chat with `bot` before `body` is sent: {"clear", "reason",
    "conversation_id", "context_tokens", "snapshot", "delay_ms", ...}, or None when no check ran or it did not finish
    in time. `uploads` are the files sent with it (none on the JSON route); a send `considered` turns away is never
    checked. `admit(c)`, when given, is the caller's own condition read with the gate. Never raises and never waits
    more than BUDGET seconds; anything short of a confident new topic is no clear."""
    if not considered(body, uploads):
        return None
    text = body.text
    started = _clock()
    deadline = started + BUDGET
    box, lock, ready = {}, threading.Lock(), threading.Event()

    def hand_over(out):
        """Give the send its outcome unless it has stopped waiting or the deadline has passed; True when it took it."""
        with lock:
            if box.get("late") or _clock() >= deadline:
                return False
            box["out"] = out
        ready.set()
        return True

    def work():
        try:
            decided = _decide(store, primary, who, bot, text, admit, deadline, box)
            if decided is None:
                ready.set()
                return
            out, detail, reserved = decided
            try:
                out["delay_ms"] = int((_clock() - started) * 1000)
                if hand_over(out):
                    detail["delay_ms"] = out["delay_ms"]
                else:                         # the send went on without it: its delay was the whole wait
                    detail.update(clear=False, error="timeout", delay_ms=box.get("waited_ms") or int(BUDGET * 1000))
                _audit(store, who, detail)
            finally:
                if reserved:
                    _release(who)
        except Exception as exc:              # a send never breaks on the check
            LOG.warning("Topic check failed: %s", type(exc).__name__)
            ready.set()
        finally:
            _WORKERS.discard(threading.current_thread())

    worker = threading.Thread(target=work, name="topic-change", daemon=True)
    _WORKERS.add(worker)
    worker.start()
    ready.wait(max(0.0, deadline - _clock()))
    with lock:
        box["late"], box["waited_ms"] = True, int((_clock() - started) * 1000)
        out = box.get("out")                  # handed over before the deadline, or not at all
    if out:
        out["delay_ms"] = int((_clock() - started) * 1000)
    return out


def _decide(store, primary, who, bot, text, admit, deadline, box):
    """The check itself, on the worker: (outcome, audit detail, whether it holds a reserved daily call), or None when
    the gate said no or the send stopped waiting before a call was made."""
    settings = store.settings
    with store.read_transaction() as c:      # one snapshot: what the gate admitted is what history and the marker read
        if admit and not admit(c):
            return None
        from .views import direct_chat
        conv = direct_chat(c, who.actor, bot)
        if not conv:
            return None
        wanted, reason, facts = gate(c, settings, bot, conv)
        if not wanted:
            return None
        qset = J.load_set(QUESTION_SET)
        engine = server_engine(store, c, primary)
        capped = engine is None and over_spend_cap(c, settings)
        turns = recent(c, who, bot, conv["id"])
        held = snapshot(c, conv["id"])
    out = {"clear": False, "conversation_id": conv["id"], "context_tokens": facts.get("context_tokens"),
           "gate": reason, "snapshot": held}
    questions = {"topic": qset["questions"]["topic"]}
    result = error = None
    left = deadline - _clock()
    if box.get("late") or left <= 0:
        return None                           # nothing asked: no call to pay for or count
    reserved = engine is not None and _reserve(store, who)
    try:
        if engine is None:
            error = "spend_cap" if capped else "unconfigured"
        elif not reserved:
            error = "budget"
        else:
            try:
                with J.within(left):
                    result = engine({"recent": turns, "message": _trim(text)}, questions, qset["label"])
            except J.JudgeError as exc:
                error = J.error_code(exc)
            except Exception as exc:          # a fake, a provider bug
                LOG.warning("Topic check failed: %s", type(exc).__name__)
                error = "error"
            if result is not None and not isinstance(result, dict):
                result, error = None, "shape"
        answer = None
        if result is not None:
            try:
                answer = J.choice(J.check_answers(result.get("answers"), questions), "topic")
            except (J.JudgeError, AttributeError, KeyError, TypeError, ValueError):
                error = "shape"
        if answer:
            out["answer"] = answer[0]
            out["clear"] = answer[0] == "new_topic" and answer[1] >= qset["thresholds"].get("new_topic", 1.01)
        out["reason"] = error or ("new_topic" if out["clear"] else (answer or ("none",))[0])
        detail = {"label": qset["label"], "questions": 1, "types": ["choice"], "gate": reason,
                  "context_tokens": out["context_tokens"], "warm": facts.get("warm", False), "clear": out["clear"]}
        if result is not None:
            detail.update(audit_detail(result))
            if answer:
                detail["answers"] = {"topic": {"choice": answer[0], "confidence": round(answer[1], 3)}}
        if error:
            detail["error"] = error
    except BaseException:
        if reserved:
            _release(who)
        raise
    return out, detail, reserved


def _audit(store, who, detail):
    """The check's `judge.call` row, written by the worker once the send has its answer."""
    try:
        with store.transaction() as c:
            H.event(c, who.actor, "judge.call", detail["label"], detail)
    except Exception as exc:
        LOG.warning("Topic check audit failed: %s", type(exc).__name__)


def apply(c, settings, who, bot, outcome):
    """In the send's own transaction, before the message is saved: clear the chat's session when the check said so
    and the chat is still as it was read (`_held`, `snapshot`). True when it cleared; a refused decision is one
    `chat.topic_kept` event."""
    from .views import clear_chat_session, direct_chat
    if not outcome or not outcome.get("clear"):
        return False
    conv = direct_chat(c, who.actor, bot)
    if not conv or conv["id"] != outcome["conversation_id"]:
        why = "chat"
    else:
        why = _held(c, conv) or (None if snapshot(c, conv["id"]) == outcome.get("snapshot") else "changed")
    if why:
        H.event(c, who.actor, "chat.topic_kept", outcome["conversation_id"], {"why": why})
        return False
    clear_chat_session(c, settings, who.actor, bot, conv["id"], {"reason": "topic_change"})
    return True


def record(c, who, message, outcome):
    """Mark the message that opened the new session (the chat's quiet "Topic changed" line) and write the measures."""
    refs = {**_refs(message), "topic_changed": True}
    c.execute("UPDATE messages SET refs=? WHERE id=?", (encode(refs), message["id"]))
    H.event(c, who.actor, "chat.topic_changed", message["conversation_id"],
            {"message_id": message["id"], "tokens_before": outcome.get("context_tokens"),
             "delay_ms": outcome.get("delay_ms", 0)})
    return H.message(c, message["id"])
