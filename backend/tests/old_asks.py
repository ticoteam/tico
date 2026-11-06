"""Ask and answer rows as older files have them. Nothing writes either kind since step 6c (a question to a person is a
question task, to a bot a DM flagged needs-action), so tests of the readers that still show them as history, and of
the asks migration, put them in directly: written as a say, then turned into the old kind."""

import json

from backend import hubdb as H


def old_ask(c, sender, recipient, body, *, conversation_id=None, refs=None, **kw):
    """An open ask from `sender` to `recipient`, as an old file holds it."""
    m = H.say(c, sender, recipient, body, conversation_id=conversation_id, refs=refs, **kw)
    c.execute("UPDATE messages SET kind='ask' WHERE id=?", (m["id"],))
    return H.message(c, m["id"])


def old_answer(c, person, ask, body, *, refs=None):
    """`person`'s answer to old ask `ask`: the reply row, and the ask marked answered."""
    m = H.say(
        c, person, ask["sender_id"], body, conversation_id=ask["conversation_id"], refs=refs, in_reply_to=ask["id"]
    )
    c.execute("UPDATE messages SET kind='answer' WHERE id=?", (m["id"],))
    c.execute("UPDATE messages SET answered_by_id=? WHERE id=?", (person, ask["id"]))
    return H.message(c, m["id"])


def with_refs(c, message_id, **refs):
    """Merge `refs` into a message's refs, as an old row may carry (questions, if_no_answer, review, spend)."""
    m = H.message(c, message_id)
    c.execute(
        "UPDATE messages SET refs=? WHERE id=?",
        (json.dumps({**(m.get("refs") or {}), **refs}, sort_keys=True), message_id),
    )
    return H.message(c, message_id)
