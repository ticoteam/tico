"""One-click clarify on a stale bot ask.

A bot's question a person never answered goes stale after two business days (hubdb.ask_older): it stays on their
list until the next weekday-morning batch sends it back to its bot (backend/still_needed.py). One tap on Clarify sends the
asking bot the winning prompt of the clarifying-prompt experiment: say whether the request is still relevant, and
if so restate it plainly. The ask itself stays open: the person has not answered, they have asked for a clearer
question. One clarify per ask: a second tap is refused, so the bot hears it once.
"""

from .store import H, Problem

ACTION = "ask.clarify"
# The experiment's winner (A, 16/16), verbatim: the measured wording, not a paraphrase.
CLARIFY_PROMPT = ("Is this request still relevant? If not, say so in one sentence and stop. If yes, restate what "
                  "you need from the person in two plain sentences: what they must decide or do first, then what "
                  "happens after. No IDs, hashes, file names, or tool names.")


def _clip(text, n=200):
    text = " ".join(str(text or "").split())
    return text if len(text) <= n else text[:n].rstrip() + "…"


def message(bot, person, quote):
    """What the tap sends the asking bot: its own question back, with the winning prompt after it."""
    return f"Please clarify this for {person}: \"{_clip(quote)}\" {CLARIFY_PROMPT}"


def already_asked(conn, ask_id):
    """Whether this ask already got its one clarify."""
    return conn.execute("SELECT 1 FROM events WHERE action=? AND target=?", (ACTION, ask_id)).fetchone() is not None


def clarify(conn, actor, ask_id):
    """Send the clarify prompt to the bot that asked `ask_id`. Returns the nudge it got."""
    asked = H.message(conn, ask_id)
    if not asked or asked["kind"] != "ask":
        raise Problem("not_found", "Question not found", 404)
    if asked["to_actor"] != actor:
        raise Problem("forbidden", "Only the person it asked can ask for a clearer question", 403)
    asker = asked["from_actor"]
    if not H.is_bot(asker):
        raise Problem("kind", "Only a bot's question can be clarified", 422)
    if not conn.execute(f"SELECT 1 FROM messages m WHERE m.id=? AND {H.OPEN_ASK_SQL}",
                        (ask_id,)).fetchone():
        raise Problem("answered", "This question was already answered", 409)
    if not H.ask_older(asked):
        raise Problem("not_stale", "This question is still fresh; answer it, or wait until it goes stale", 422)
    if already_asked(conn, ask_id):
        raise Problem("already_requested", "The bot was already asked to clarify this question", 409)
    person = (H.human(conn, H.actor_id(asked["to_actor"])) or {}).get("name") or H.actor_id(asked["to_actor"])
    text = message(asker, person, asked.get("body"))
    task_id = H.message_task_id(asked, H.conversation(conn, asked["conversation_id"]))
    task = H.task(conn, task_id) if task_id else None
    refs = {"clarify": ask_id, **({"task": task["id"]} if task else {})}
    msg = H.say(conn, actor, asker, text, conversation_id=asked["conversation_id"], refs=refs)
    if task:
        # As with a comment meant for the bot: it wakes on the task, whose clock restarts, but what the person
        # owes it does not change: the wait stays theirs until they answer.
        conn.execute("UPDATE tasks SET updated_at=? WHERE id=?", (H.now(), task["id"]))
        conn.execute("INSERT INTO task_delegations(task_id,delegate,requested_by,message_id,expires_at) VALUES(?,?,?,?,?)",
                     (task["id"], asker, actor, msg["id"], H.shift(H.now(), hours=24)))
    H.event(conn, actor, ACTION, ask_id, {"to": actor, "bot": asker, **({"task": task["id"]} if task else {})})
    return {"bot": asker, "task": task["id"] if task else None, "message": msg["id"]}
