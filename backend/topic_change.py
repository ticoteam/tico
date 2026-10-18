"""Topic changed: before a person's message in their direct chat with a bot is sent, a Decide check of whether it starts
a new topic (`questions/topic-change.json`, through the server's Decide engine, `backend/judge.py` `server_engine`).
On a confident new topic the session is cleared exactly as the person's own Clear does (`views.clear_chat_session`)
and the message carries `refs.topic_changed`, which the chat shows as a quiet "Topic changed" line above it.

The gate is plain code, read from what runners already report. The session is the bot's provider thread the message
would continue (its `bot_sessions` row); its last turn is the newest finished turn on that thread.
- Warm: that turn finished under WARM_SECONDS ago and reported `cached_tokens` > 0. Anything else is cold: no turn, an
  older one, or a harness that reports no cache use (Grok, today).
- Context size: that turn's `input_tokens + cached_tokens`, the last prompt.
- Check at BIG_CONTEXT tokens or more, or when the cache is cold; not under BIG_CONTEXT with a warm cache.
- Never when the team's `topic_fresh` rule is off, the chat has an active goal, a turn is running, or the chat has fewer
  than MIN_TURNS turns since its last clear.

Fail open: no decision model, rehearsal, the spend cap, the daily call budget, an error, no answer within BUDGET seconds,
`not_sure`, `same_topic` or a `new_topic` under the set's threshold all send the message in the same session with no
line. Each check is one `judge.call` row in the person's name, counted against their daily Decide calls as the Slack
gateway's routing is (label, model, tokens, latency, cost, the answer, the outcome, the delay); never the chat's text. Each clear is one `chat.topic_changed` event: the tokens before the clear and the delay added
before the send."""

import logging
import threading
import time

from clients import judge as J
from . import team_rules
from .judge import audit_detail, daily_cap, over_spend_cap, server_engine, used_today
from .store import H, encode

LOG = logging.getLogger("tico.topic_change")
QUESTION_SET = "topic-change"
RULE = "topic_fresh"
# The most a send waits for the check, all of it: the read, the call and the answer.
BUDGET = 1.5
# The default prompt-cache lifetime: a session whose last turn read the cache this recently is warm.
WARM_SECONDS = 300
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
    config = c.execute("SELECT config_json FROM bot_config WHERE bot=?", (bot,)).fetchone()
    cfg = follow(c, bot, H._json(config[0], {}) or {}) if config else {}
    runtime, model = providers.bot_choice(c, settings, cfg)
    session = c.execute("SELECT thread_id FROM bot_sessions WHERE bot=? AND runtime=? AND model=?",
                        (bot, runtime, model)).fetchone()
    if not session or not session[0]:
        return None
    row = c.execute("SELECT finished,input_tokens,cached_tokens FROM turns WHERE bot=? AND thread_id=? "
                    "AND finished IS NOT NULL ORDER BY finished DESC LIMIT 1", (bot, session[0])).fetchone()
    return dict(row) if row else None


def warmth(turn, now=None):
    """(warm, context tokens) of a session from its last turn (`last_turn`)."""
    if not turn:
        return False, 0
    cached = int(turn.get("cached_tokens") or 0)
    context = int(turn.get("input_tokens") or 0) + cached
    finished, at = H.parse_ts(turn.get("finished")), H.parse_ts(now or H.now())
    age = (at - finished).total_seconds() if finished and at else None
    return cached > 0 and age is not None and 0 <= age < WARM_SECONDS, context


def epoch_since(c, conversation_id):
    """When the chat's session was last cleared, or None."""
    row = c.execute("SELECT updated FROM session_epochs WHERE conversation_id=?", (conversation_id,)).fetchone()
    return row[0] if row else None


def turns_since(c, bot, conversation_id, since):
    """The bot's finished turns on this chat since its last clear."""
    return c.execute("SELECT count(*) FROM turns t JOIN messages m ON m.id=t.message_id WHERE t.bot=? "
                     "AND m.conversation_id=? AND t.finished IS NOT NULL AND t.started>?",
                     (bot, conversation_id, since or "")).fetchone()[0]


def gate(c, settings, bot, conversation_id):
    """(check, reason, facts) for a message about to go into this direct chat. `reason` says why it is or is not
    checked; `facts` are the tokens and warmth the measures read."""
    from .chat_goals import current
    from .views import chat_running
    if not team_rules.load(c)[RULE]:
        return False, "off", {}
    goal = current(c, conversation_id)
    if goal and goal.get("status") == "active":
        return False, "goal", {}
    if chat_running(c, conversation_id):
        return False, "running", {}
    if turns_since(c, bot, conversation_id, epoch_since(c, conversation_id)) < MIN_TURNS:
        return False, "few_turns", {}
    warm, context = warmth(last_turn(c, settings, bot))
    facts = {"warm": warm, "context_tokens": context}
    if context >= BIG_CONTEXT:
        return True, "big", facts
    if not warm:
        return True, "cold", facts
    return False, "warm", facts


def _refs(message):
    refs = message.get("refs")
    if refs is None:
        refs = H._json(message.get("refs_json"), {})
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
        sender = message.get("from_actor")
        if sender == who.actor:
            turns.append({"person": _trim(message.get("body")), "bot": ""})
        elif sender == "bot:" + bot and turns:
            turns[-1]["bot"] = _trim((turns[-1]["bot"] + " " + str(message.get("body") or "")).strip())
    return turns[-RECENT_TURNS:]


def _ask(engine, state, questions, label, budget):
    """The engine's answer within `budget` seconds: (result, None), (None, error code) or (None, 'timeout'). A call
    still running at the deadline finishes on its own thread; its cost is metered all the same."""
    box = {}

    def work():
        try:
            with J.within(budget):
                box["result"] = engine(state, questions, label)
        except J.JudgeError as exc:
            box["error"] = J.error_code(exc)
        except Exception as exc:    # a fake, a provider bug: a send never breaks on it
            LOG.warning("Topic check failed: %s", type(exc).__name__)
            box["error"] = "error"
    worker = threading.Thread(target=work, name="topic-change", daemon=True)
    worker.start()
    worker.join(max(0.0, budget))
    if worker.is_alive():
        return None, "timeout"
    return box.get("result"), box.get("error")


def check(store, primary, who, bot, text):
    """Whether to clear this person's direct chat with `bot` before `text` is sent: {"clear": bool, "reason",
    "conversation_id", "context_tokens", "delay_ms"}, or None when no check ran (not a direct chat, the gate said no).
    Never raises and never takes more than BUDGET seconds; anything short of a confident new topic is no clear."""
    started = time.monotonic()
    settings = store.settings
    try:
        with store.read() as c:
            from .views import direct_chat
            conv = direct_chat(c, who.actor, bot)
            if not conv:
                return None
            wanted, reason, facts = gate(c, settings, bot, conv["id"])
            if not wanted:
                return None
            qset = J.load_set(QUESTION_SET)
            engine = server_engine(store, c, primary)
            capped = engine is None and over_spend_cap(c, settings)
            budget_used = engine is not None and used_today(c, who.actor) >= daily_cap(settings, who.role)
            turns = recent(c, who, bot, conv["id"])
    except Exception as exc:                  # the gate's own reads: the message goes as before
        LOG.warning("Topic check skipped: %s", type(exc).__name__)
        return None
    out = {"clear": False, "conversation_id": conv["id"], "context_tokens": facts.get("context_tokens", 0),
           "gate": reason}
    questions = {"topic": qset["questions"]["topic"]}
    result = error = None
    if engine is None:
        error = "spend_cap" if capped else "unconfigured"
    elif budget_used:
        error = "budget"
    else:
        state = {"recent": turns, "message": _trim(text)}
        result, error = _ask(engine, state, questions, qset["label"], BUDGET - (time.monotonic() - started))
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
    out["delay_ms"] = int((time.monotonic() - started) * 1000)
    detail = {"label": qset["label"], "questions": 1, "types": ["choice"], "gate": reason,
              "context_tokens": out["context_tokens"], "warm": facts.get("warm", False),
              "clear": out["clear"], "delay_ms": out["delay_ms"]}
    if result is not None:
        detail.update(audit_detail(result))
        if answer:
            detail["answers"] = {"topic": {"choice": answer[0], "confidence": round(answer[1], 3)}}
    if error:
        detail["error"] = error
    try:
        with store.transaction() as c:
            H.event(c, who.actor, "judge.call", qset["label"], detail)
    except Exception as exc:
        LOG.warning("Topic check audit failed: %s", type(exc).__name__)
    return out


def apply(c, settings, who, bot, outcome):
    """In the send's own transaction, before the message is saved: clear the chat's session when the check said so
    and nothing has changed since (no turn started, still the same chat). True when it cleared."""
    from .views import chat_running, clear_chat_session, direct_chat
    if not outcome or not outcome.get("clear"):
        return False
    conv = direct_chat(c, who.actor, bot)
    if not conv or conv["id"] != outcome["conversation_id"] or chat_running(c, conv["id"]):
        return False
    clear_chat_session(c, settings, who.actor, bot, conv["id"], {"reason": "topic_change"})
    return True


def record(c, who, message, outcome):
    """Mark the message that opened the new session (the chat's quiet "Topic changed" line) and write the measures."""
    refs = {**_refs(message), "topic_changed": True}
    c.execute("UPDATE messages SET refs_json=? WHERE id=?", (encode(refs), message["id"]))
    H.event(c, who.actor, "chat.topic_changed", message["conversation_id"],
            {"message_id": message["id"], "tokens_before": outcome.get("context_tokens", 0),
             "delay_ms": outcome.get("delay_ms", 0)})
    return H.message(c, message["id"])
