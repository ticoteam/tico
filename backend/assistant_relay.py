# pyright: strict
"""Another agent's answer to what the person's Assistant asked it for them comes back to the person's Assistant chat.

The Assistant asks an agent by writing in the person's own chat with that agent, as the person (`refs.via` assistant,
backend/assistant.py). The agent's first reply there after that message is relayed once into the person's Assistant
chat: a short summary and a link to the full reply, read with the person's own access. Written as the reply is written
(`hubdb._write_message`), in the same transaction, with no run; the relay's wake key, one per asked message, is its
dedupe record (task_effects.assistant_line, as the report back writes).
"""

from typing import TYPE_CHECKING, Any, cast

if TYPE_CHECKING:
    import sqlite3

Row = dict[str, Any]
SUMMARY_CHARS = 280

# The person's latest message in the chat (messages_thread_human): the one the agent's reply answers.
_ASKED_SQL = (
    "SELECT id, sender_id, recipient_id, kind, refs FROM messages WHERE conversation_id=? AND sender_id LIKE 'human:%' "
    "AND id<>? AND deleted_at IS NULL ORDER BY seq DESC LIMIT 1"
)


def replied(conn: "sqlite3.Connection", reply: Row) -> None:
    """An agent wrote `reply` to a person: when the person's latest message in that chat is one their Assistant wrote to
    this agent for them (not a notice, which wants no answer), and nothing was relayed for it yet, relay it."""
    from . import hubdb

    H: Any = hubdb
    agent, person = str(reply.get("sender_id") or ""), str(reply.get("recipient_id") or "")
    slug = str(getattr(getattr(conn, "settings", None), "assistant_bot", "") or "assistant")
    if agent == "bot:" + slug or not reply.get("conversation_id"):
        return
    asked = conn.execute(_ASKED_SQL, (reply["conversation_id"], reply["id"])).fetchone()
    if not asked or asked["sender_id"] != person or asked["recipient_id"] != agent or asked["kind"] != "say":
        return
    refs: object = H._json(asked["refs"], {})
    if not isinstance(refs, dict) or cast(Row, refs).get("via") != "assistant":
        return
    key = f"assistant_relay:{asked['id']}"
    if conn.execute("SELECT 1 FROM messages WHERE wake_key=?", (key,)).fetchone():
        return  # only the first reply is relayed
    from . import batch_work

    work: Any = batch_work
    with work.isolated(conn, "assistant_relay", asked["id"], private=True):
        _relay(conn, reply, person, key)


def _relay(conn: "sqlite3.Connection", reply: Row, person: str, key: str) -> None:
    from . import hubdb, task_effects, task_privacy

    H: Any = hubdb
    privacy: Any = task_privacy
    name = task_effects.plain_line(H.actor_name(conn, reply["sender_id"]))
    chat = f"#/thread/{reply['conversation_id']}"
    # A system line: names in it mention nobody and it starts no run.
    refs: Row = {"assistant": True, "assistant_relay": True, "quiet": True, "no_mentions": True}
    words = " ".join(str(reply.get("body") or "").split())
    if words and privacy.message_readable(conn, person, reply):
        cut = words[:SUMMARY_CHARS].rstrip() + ("…" if len(words) > SUMMARY_CHARS else "")
        body = f"{name} replied: {task_effects.plain_line(cut)}\n\n[Open the reply]({chat}?m={reply['id']})"
    else:
        # Nothing to quote, or a reply the person cannot read: where it is, and nothing of it.
        body = f"{name} replied in your chat with them\n\n[Open the chat]({chat})"
    task_effects.assistant_line(conn, person, body, refs, key)
