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
                        "state": {"enum": ["scheduled", "live", "ending", "ended"]},
                        "connector": "s",
                        "started_at": "s",
                        "ended_at": "n",
                        "end_reason": "n",
                        "scheduled_start": "n",
                        "scheduled_end": "n",
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
                    "enum": ["scheduled", "live", "ending", "ended"],
                    "description": "scheduled: booked for later, started by its first join. ending: End was pressed "
                    "and the speech already heard is being transcribed",
                },
                "connector": "s",
                "meeting_id": "s",
                "started_at": "s",
                "ended_at": "n",
                "end_reason": "n",
                "participants": items(ref("CallParticipant")),
                "bots": items({"type": "string"}),
                "scheduled_start": {"type": ["string", "null"], "description": "When a scheduled call is booked for"},
                "scheduled_end": "n",
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
            model={"type": ["string", "null"], "description": "Legacy field. Bots use their own models."},
            effort={"type": ["string", "null"], "description": "The effort used for voice replies, when enabled."},
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
            stage={
                "anyOf": [
                    obj(
                        {
                            "kind": {"enum": ["screen", "file"]},
                            "by": {"type": "string", "description": "Who shows it"},
                            "at": "n",
                        },
                        required=["kind", "by", "at"],
                        file_id={"type": "string", "description": "A file you may read; absent with no_access"},
                        version={"type": "integer", "description": "The file's version that shows"},
                        title={"type": "string"},
                        no_access={"type": "boolean", "description": "A file you may not read: nothing names it"},
                    ),
                    {"type": "null"},
                ],
                "description": "What shows on a live Tico Meet call's stage, as you may see it; null for nothing",
            },
            stage_n={
                "type": "integer",
                "description": "How many times the stage changed: a read with a lower count is older than one with a higher",
            },
        ),
        "Call": {"allOf": [ref("CallBase"), ref("CallProvider")]},
        "CallList": obj(
            {
                "calls": items(ref("CallSummary")),
                "next_before": "n",
                "available": "b",
                "providers": items({"enum": ["google_meet", "tico"]}),
                "next_scheduled": {
                    "anyOf": [{"allOf": [ref("CallSummary"), obj({"can_edit": "b"})]}, {"type": "null"}],
                    "description": "The thread's soonest scheduled call, whatever the page; null when none",
                },
            }
        ),
        "CallRinging": obj({"calls": items(ref("Call"))}),
        "CallLive": obj({"calls": items(ref("Call"))}),
        "CallUpcoming": obj(
            {
                "calls": items(
                    obj(
                        {
                            "id": "s",
                            "title": "s",
                            "thread_id": "s",
                            "scheduled_start": "s",
                            "scheduled_end": "s",
                            "can_edit": {
                                "type": "boolean",
                                "description": "Whether you may change its time or cancel it (its booker, or who manages its thread)",
                            },
                            "people": items(
                                {
                                    "type": "string",
                                    "description": "Up to 8 of the people the call is for, its booker first",
                                }
                            ),
                        }
                    )
                )
            }
        ),
        "CallJoin": obj(
            {"call": ref("Call"), "url": "n", "expires_in": "i", "token": "s"},
            required=["call", "url"],
        ),
        "CallToken": obj({"url": "s", "expires_in": "i", "token": "s"}),
        "CallBotNotes": obj({"text": "s"}),
        "CallBotNoteWritten": obj({"ok": "b"}),
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
