# pyright: strict
"""The answers of the Assistant routes (backend/openapi_v2.py gathers them)."""

from ..api_contract import ref
from ._base import ACTORS, SCHEMAS, items, obj

SCHEMAS.update(
    {
        "Assistant": obj(
            {
                "available": "b",
                "state": "s",
                "bot": "s",
                "name": "s",
                "can_turn_on": "b",
                "room_id": "n",
                "messages": items(ref("Message")),
                "has_more": "b",
                "next_before": "n",
                "execution": {
                    "oneOf": [ref("Execution"), {"type": "null"}],
                    "description": "The assistant's current run: state, label, "
                    "text so far; null when it is not working (show a thinking state while it is)",
                },
                "actions": {
                    "type": "object",
                    "description": "{action id: proposal} for every card in `messages` "
                    "(a message whose refs.action names one is a Confirm / Cancel card)",
                },
                "pending": "a",
                "earlier": {
                    "oneOf": [
                        obj({"room_id": "s", "bot": "s", "name": "s"}, required=["room_id", "bot", "name"]),
                        {"type": "null"},
                    ],
                    "description": "The owner's earlier chat with the Strategy Owner, read-only (GET "
                    "/api/v2/conversations/{room_id}/messages); null for everyone else, or none",
                },
            },
            required=[
                "available",
                "state",
                "bot",
                "name",
                "can_turn_on",
                "room_id",
                "messages",
                "has_more",
                "next_before",
                "execution",
                "actions",
                "pending",
                "earlier",
            ],
            actors=ACTORS,
        ),
        "AssistantSent": obj({"message": ref("Message")}, required=["message"]),
        "AssistantActionResult": obj(
            {
                "action": {
                    "type": "object",
                    "description": "id, summary, method, path, body, status "
                    "(pending, running, done, failed, canceled, expired), result",
                },
                "message_id": "s",
                "shown_once": {
                    "type": "object",
                    "description": "Confirm only, on a card that makes a "
                    "one-time value (a new computer's code): {code, expires, operator}, "
                    "in this answer alone and never stored on the card",
                },
            },
            required=["action"],
        ),
        "AssistantChangeList": obj({"changes": items({"type": "object"}), "next": {"type": ["string", "null"]}}),
        "AssistantChangeResult": obj({"id": "s", "restored": "b", "already": "b", "object": "s"}),
    }
)
