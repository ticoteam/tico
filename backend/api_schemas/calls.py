# pyright: strict
"""The answers of the Calls routes (backend/openapi_v2.py gathers them)."""

from ..api_contract import ref
from ._base import SCHEMAS, items, obj

SCHEMAS.update(
    {
        # Calls (docs/meet.md): a call belongs to a thread. Joining a Tico Meet call hands back a token for audio and screen share
        # only; joining a Google Meet hands back its link.
        "CallParticipant": obj({"actor": "s", "joined_at": "s", "left_at": "n", "removed_at": "n", "in_call": "b"}),
        "CallProvider": obj(
            {
                "provider": {"enum": ["tico", "google_meet"]},
                "meet_uri": {"type": ["string", "null"], "description": "The Google Meet link Google gave"},
                "join_url": {
                    "type": ["string", "null"],
                    "description": "The Meet link with authuser=<your Google address> when known; null for Tico Meet",
                },
                "live_count": {"type": "integer", "description": "People in the call now, as its status source says"},
                "status_source": {"enum": ["google", None], "description": "Where live status comes from"},
                "seen_at": {"type": ["string", "null"], "description": "When anyone was first seen in the Meet"},
                "duration_s": {"type": ["integer", "null"], "description": "An ended call's length"},
                "ring": {"type": "boolean", "description": "The call rings you: rung, not joined, still live"},
                "card_message_id": "n",
                "transcript_message_id": "n",
            }
        ),
        "CallHold": obj(
            {"kind": {"enum": ["transcript", "action_items"]}, "reason": "s", "detail": "s", "created_at": "s"}
        ),
        "CallSummary": {
            "allOf": [
                obj(
                    {
                        "id": "s",
                        "thread_id": "s",
                        "title": "s",
                        "state": {"enum": ["live", "ended"]},
                        "connector": "s",
                        "started_at": "s",
                        "ended_at": "n",
                        "end_reason": "n",
                    }
                ),
                ref("CallProvider"),
            ]
        },
        "CallBase": obj(
            {
                "id": "s",
                "thread_id": "s",
                "title": "s",
                "state": {"enum": ["live", "ended"]},
                "connector": "s",
                "meeting_id": "s",
                "started_at": "s",
                "ended_at": "n",
                "end_reason": "n",
                "participants": items(ref("CallParticipant")),
                "bots": items({"type": "string"}),
            },
            holds={
                "type": "array",
                "items": ref("CallHold"),
                "description": "Posts the thread refused at the call's end. Only the connector sees them",
            },
            speak_replies={
                "type": "boolean",
                "description": "Whether bots were asked to speak their replies "
                "on this call; off unless the call asked. Spoken replies are not sent yet",
            },
        ),
        "Call": {"allOf": [ref("CallBase"), ref("CallProvider")]},
        "CallList": obj(
            {
                "calls": items(ref("CallSummary")),
                "next_before": "n",
                "available": "b",
                "providers": items({"enum": ["google_meet", "tico"]}),
            }
        ),
        "CallRinging": obj({"calls": items(ref("Call"))}),
        "CallJoin": obj(
            {"call": ref("Call"), "url": "n", "expires_in": "i", "token": "s"},
            required=["call", "url"],
        ),
        "CallToken": obj({"url": "s", "expires_in": "i", "token": "s"}),
    }
)
