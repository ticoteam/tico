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
                "status_source": {
                    "enum": ["google", "recorder", None],
                    "description": "Where live status comes from: Google, or the Tico Meet room as LiveKit lists it",
                },
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
                        "state": {"enum": ["live", "ending", "ended"]},
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
                "state": {
                    "enum": ["live", "ending", "ended"],
                    "description": "ending: End was pressed and the speech already heard is being transcribed",
                },
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
                "description": "Whether the call's bots answer aloud as well as in the thread: on for Tico Meet unless "
                "the start turned it off, off for Google Meet",
            },
            voice={
                "anyOf": [
                    obj(
                        {
                            "bots": items(
                                obj(
                                    {
                                        "slug": "s",
                                        "state": {
                                            "enum": ["listening", "waiting", "thinking", "making_voice", "speaking"]
                                        },
                                        "caption": {
                                            "type": ["string", "null"],
                                            "description": "The sentence the bot is saying now",
                                        },
                                    }
                                )
                            ),
                            "note": {
                                "type": ["string", "null"],
                                "description": "A short note for the people in the call, such as Please repeat that",
                            },
                        }
                    ),
                    {"type": "null"},
                ],
                "description": "A live voice call's bots: what each is doing and saying. null for any other call",
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
        # The meeting window's notes and ringing (backend/call_notes.py).
        "CallNotes": obj(
            {
                "pending": {"type": "boolean", "description": "The call still owes its notes"},
                "message_id": "n",
                "summary": items({"type": "string"}),
                "items": items(
                    obj({"title": "s", "owner": {"type": "string", "description": "Who took it on; '' for nobody"}})
                ),
            }
        ),
        "CallRing": obj({"actor": "s", "added": {"type": "boolean", "description": "Added to the thread to be rung"}}),
    }
)
