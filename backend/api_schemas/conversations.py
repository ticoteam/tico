# pyright: strict
"""The answers of the Conversations routes (backend/openapi_v2.py gathers them)."""

from ..sources import KINDS as SOURCE_KINDS
from ..api_contract import ref
from ._base import ACTORS, SCHEMAS, items, obj

# Who wrote a line (backend/sources.py): a message's, a search hit's and a task history entry's `source`.
SOURCE_SCHEMA = obj(
    {"kind": {"type": "string", "enum": list(SOURCE_KINDS)}, "name": "s", "link": "n"},
    required=["kind", "name", "link"],
)


SCHEMAS.update(
    {
        "MessageSource": SOURCE_SCHEMA,
        "ChatGoal": obj(
            {
                "id": "s",
                "conversation_id": "s",
                "bot": "s",
                "objective": "s",
                "status": "s",
                "note": "s",
                "set_by": "s",
                "set_at": "s",
                "updated_at": "s",
                "ended_at": "n",
            },
            required=[
                "id",
                "conversation_id",
                "bot",
                "objective",
                "status",
                "note",
                "set_by",
                "set_at",
                "updated_at",
                "ended_at",
            ],
        ),
        "CatchUpResult": obj(
            {"thread_id": "s", "scope": "s", "messages": "i", "left_out": "i", "posted": "o", "words": "s"},
            required=["thread_id", "scope", "messages", "left_out"],
        ),
        "ChatGoalResult": obj(
            {"goal": {"anyOf": [ref("ChatGoal"), {"type": "null"}]}},
            required=["goal"],
            supported={"type": "boolean"},
            commands=items({"type": "object"}),
        ),
        "Message": obj(
            {
                "id": "s",
                "conversation_id": "s",
                "sender_id": "s",
                "recipient_id": "n",
                "kind": "s",
                "body": "s",
                "created_at": "s",
                "in_reply_to": "n",
                "refs": "o",
            },
            required=[
                "id",
                "conversation_id",
                "sender_id",
                "recipient_id",
                "kind",
                "body",
                "created_at",
                "in_reply_to",
                "refs",
            ],
            sender_name={"type": "string", "description": "Display name of sender_id, when it is a person or a bot"},
            recipient_name={"type": "string", "description": "Display name of recipient_id"},
            sender_can_approve_send={
                "type": "boolean",
                "description": "On GET /messages/{mid}: whether the human sender "
                "currently may manage the bot addressee's mail sending (Auth.bot_manager); false for bot senders "
                "or non-bot addressees. This checks authority only, not send intent or the draft's contents",
            },
            body_raw={
                "type": "string",
                "description": "A notice Tico wrote, as stored (with actor ids); `body` shows names to people",
            },
            edited_at={
                "type": ["string", "null"],
                "description": "When the author last changed a task comment's text; null when never edited",
            },
            deleted_at={
                "type": ["string", "null"],
                "description": "When the author deleted a task comment. A delete or "
                "retry acknowledgment can include a tombstone with an empty body; deleted comments are never listed",
            },
            run={
                "type": "object",
                "description": "The run that handled this message, once one has: on a person's message "
                "`{job_id, run_id, state}` where `state` is `started_run` (it started the run) or `added_to_run` "
                "(it was folded into a run already working); on a bot's reply `{job_id, run_id}` (the run that "
                "wrote it, plus a summary of what it did). Absent until a run takes the message",
                "properties": {
                    "job_id": {"type": "string"},
                    "run_id": {"type": "string"},
                    "state": {"enum": ["started_run", "added_to_run"]},
                },
            },
            ask=ref("TaskReviewAskView"),
            answer=ref("ReviewAnswer"),
            answers=items({"oneOf": [{"type": "string"}, ref("ReviewAnswer")]})
            | {
                "description": "On a structured ask: all review answers, oldest first. On a bot reply: handled message ids"
            },
            woke={"type": "boolean", "description": "On a send: false when the message was held (see `held`)"},
            held={
                "type": "string",
                "description": "On a send: a bot's message to a bot held for that bot's next "
                "run, and how to wake it now (backend/bot_to_bot.py)",
            },
            sent_as=ref("SentAs")
            | {
                "description": "On a send to a bot: the way it went (steer, queue or defer) "
                "and a defer's deadline (backend/delivery.py)"
            },
        ),
        "Execution": obj(
            {"job_id": "s", "message_id": "s", "bot_id": "s", "run_id": "n", "state": "s", "label": "s", "text": "s"},
            required=["job_id", "message_id", "bot_id", "run_id", "state", "label", "text", "parts"],
            parts=items(obj({"kind": {"enum": ["progress", "reply", "tool"]}, "text": "s", "at": "s"}))
            | {
                "description": "The run's pieces so far, in order, while it is leased or running (empty otherwise). "
                "`reply` and `progress` are what the bot wrote (`progress` when a tool call follows it); "
                "`tool` is a short label such as \"Filing a task\" or \"Ran npm\", never its arguments or output"
            },
        ),
        "Conversation": obj(
            {
                "id": "s",
                "kind": "s",
                "subject": "s",
                "participants": items({"type": "string"}),
                "created_at": "s",
                "last_message_at": "s",
                "closed_at": "n",
            }
        ),
        "ConversationList": obj(
            {"actor": "s", "conversations": items(ref("Conversation"))},
            required=["actor", "conversations"],
            actors=ACTORS,
        ),
        "MessagePage": obj(
            {
                "conversation": ref("Conversation"),
                "messages": items(ref("Message")),
                "has_more": "b",
                "next_before": "n",
            },
            required=["conversation", "messages", "has_more", "next_before"],
            actors=ACTORS,
        ),
        "Snapshot": obj(
            {
                "messages": items(ref("Message")),
                "has_more": "b",
                "next_before": "n",
                "execution": {
                    "oneOf": [ref("Execution"), {"type": "null"}],
                    "description": "The latest run: state, label, "
                    "text (the reply so far; separate messages are joined by a blank line), parts, bot",
                },
            }
        ),
        "MessageResult": obj({"message": ref("Message")}),
        "ChatResult": obj({"conversation": ref("Conversation"), "message": ref("Message")}),
        # The way a send went (backend/delivery.py): steer, queue or defer, and a defer's deadline (null: none).
        "SentAs": obj(
            {"delivery": {"type": "string", "enum": ["steer", "queue", "defer"]}, "until": "n"},
            required=["delivery", "until"],
        ),
    }
)

SCHEMAS["Message"]["properties"]["source"] = ref("MessageSource")
