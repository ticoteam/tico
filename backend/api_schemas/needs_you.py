# pyright: strict
"""The answers of the Needs you routes (backend/openapi_v2.py gathers them)."""

from typing import Any

from ..api_contract import ref
from ._base import SCHEMAS, items, obj


def described(fields: dict[str, Any], description: str) -> dict[str, Any]:
    """An object schema of `fields` (obj), every one of them present, with its description."""
    return {**obj(fields), "description": description}


# One item of a person's Needs you (backend/views.py needs_items).
NEEDS_ITEM = obj(
    {
        "id": "s",
        "kind": {
            "type": "string",
            "enum": ["task", "ask", "paused", "question", "access", "key", "key_drop"],
            "description": "task: a private task they must let someone see (`task.turn.share`); ask: a requirement "
            "with their name (its fields in "
            "`ask`); paused: a bot that cannot run, which they can fix; question: a bot's ❓ on its message for "
            "them, until they reply in that chat; access: an access request they can grant (`access`); key: a key "
            "card they can fill (`key`); key_drop: the Tools owner's key proposal they can grant (`key_drop`). An @ "
            "alone is not here: it is in the threads sidebar",
        },
        "line": "s",
        "since": "n",
        "task": {"oneOf": [ref("TaskBrief"), {"type": "null"}], "description": "The brief task it is about"},
    },
    required=["id", "kind", "line", "since"],
    ask={"oneOf": [ref("TaskRequirement"), {"type": "null"}]},
    task_id={"type": ["string", "null"]},
    requirement_id={"type": ["string", "null"]},
    message_id={"type": "string", "description": "A ❓'s message"},
    mark_id={"type": "string", "description": "A ❓'s mark (POST /api/v2/questions/{mid}/give)"},
    shows={
        "type": "array",
        "items": {"type": "string"},
        "description": "A ❓'s website browser to take over (`browser:<tool>/<account>`)",
    },
    from_id={
        "type": ["string", "null"],
        "description": "Who asks: a ❓'s bot, the bot an access request or key card is for, the Tools owner of a key proposal",
    },
    access=described(
        {
            "id": "s",
            "state": "s",
            "subject": "s",
            "subject_name": "s",
            "resource_type": {"type": "string", "enum": ["tool", "repository"]},
            "resource_name": "s",
            "level": {"type": "string", "enum": ["read", "read_write"]},
            "why": "s",
        },
        "An access request's card (backend/access_requests.py card_of): POST "
        "/api/v2/access-requests/{id}/grant (with level read: Read only) or /decline",
    ),
    key=described(
        {"id": "s", "title": "s", "label": "s", "env": "s", "format": "s", "help_url": "s", "kind": "s"},
        "A key card (backend/credential_cards.py card_view): POST /api/v2/credential-requests/{id}/save",
    ),
    key_drop=described(
        {
            "message_id": "s",
            "state": "s",
            "losses": "s",
            "rows": items(
                obj(
                    {
                        "at": {
                            "type": "integer",
                            "description": "The row's place on the card: what Grant's keep names",
                        },
                        "key": "s",
                        "tool_name": "s",
                        "level": {"type": "string", "enum": ["read", "read_write"]},
                        "bots": items(obj({"id": "s", "name": "s"})),
                        # the tool's key at that level this one replaces: who keeps their access on it, who loses it
                        "replaces": {
                            "oneOf": [obj({"key": "s", "keeps": items("s"), "loses": items("s")}), {"type": "null"}]
                        },
                    }
                )
            ),
        },
        "A key proposal's card (backend/key_grants.py): POST "
        "/api/v2/tools-chat/proposals/{message_id}/grant with the places of the rows kept (`keep`, each row's `at`), "
        '`positions: "original"` (keep names places on the card as proposed: without it, a card with a row gone '
        "answers 409) and `seen` (`losses`, the loss preview shown: 409 when it moved)",
    ),
    conversation_id={"type": "string", "description": "The thread a ❓ was written in"},
    root_id={"type": "string", "description": "A ❓'s root thread: the rail row it moves"},
    href={"type": "string", "description": "A paused cause a person fixes in the app: where (Connect GitHub)"},
    snoozed_until={
        "type": "string",
        "description": "In GET /api/v2/needs-you/snoozed: when this snoozed ask comes back",
    },
    about={
        "type": "string",
        "description": "An OK whose task is titled by its kind alone (\"Mail a letter\"): what this one is about, "
        "in its subject's words (\"Mail a letter to Ana Diaz\"; a Finance action's payee and amount), for its party; "
        "a page ask's submitted page title (its `line` too), for a reader of that version; the row's first line",
    },
)


SCHEMAS.update(
    {
        "NeedsYouItem": NEEDS_ITEM,
        "NeedsYou": {
            "anyOf": [
                obj(
                    {"actor": "s", "items": items(ref("NeedsYouItem"))},
                    required=["actor", "items"],
                    count={"type": "integer", "description": "Items in the whole list, whatever page this is"},
                    by_kind={
                        "type": "object",
                        "additionalProperties": {"type": "integer"},
                        "description": "Items of each kind in the whole list, including unread pages",
                    },
                    next={
                        "type": ["string", "null"],
                        "description": "With limit or cursor: pass as cursor for the next page; null on the last page",
                    },
                    bots={
                        "type": "object",
                        "additionalProperties": {"type": "integer"},
                        "description": "With limit or cursor: how many items in the whole list each bot is behind",
                    },
                    stages={
                        "type": "object",
                        "additionalProperties": {"type": "integer"},
                        "description": "With limit or cursor: how many asks, tasks and mentions in the whole list wait in "
                        "each stage (by stage id), for a stage's review",
                    },
                    restart={
                        "type": "boolean",
                        "description": "The cursor was from an older version: this page is the first",
                    },
                    snoozed_until={
                        "type": ["string", "null"],
                        "description": "When the first ask the caller snoozed for themselves comes back: read the list again "
                        "then (POST /api/v2/needs-you/snooze)",
                    },
                ),
                obj(
                    {"actor": "s", "count": "i"},
                    flow_count={"type": "integer", "description": "Distinct task rows plus requests in Flow"},
                    snooze_refresh_in_ms={
                        "type": ["integer", "null"],
                        "description": "Server-calculated milliseconds until the next snoozed ask returns, or null",
                    },
                ),
            ]
        },
        "FlowOpinions": obj(
            {
                "opinions": items(
                    obj(
                        {
                            "id": "s",
                            "state": {"type": "string", "enum": ["ready", "pending", "none"]},
                            "score": {"type": ["integer", "null"], "minimum": 0, "maximum": 100},
                        }
                    )
                )
            }
        ),
        "SetupGaps": obj(
            {
                "areas": items(
                    obj(
                        {
                            "area": {
                                "type": "string",
                                "enum": ["market", "strategy", "goals", "org", "tools", "budget"],
                            },
                            "needs": "b",
                            "bot": {"oneOf": [obj({"id": "s", "slug": "s", "name": "s"}), {"type": "null"}]},
                            "href": "s",
                        }
                    )
                )
            }
        ),
        "FlowPinged": obj(
            {
                "pinged": items(obj({"id": "s", "name": "s"})),
                "recent": items(obj({"id": "s", "name": "s"})),
            }
        ),
        "TaskBrief": obj(
            {
                "id": "s",
                "number": {"type": ["integer", "null"]},
                "title": "s",
                "status": {"type": "string", "enum": ["open", "done", "closed"]},
                "version": "i",
                "pipeline_id": "n",
                "stage_id": "n",
                "owner_id": "n",
                "assignee_id": "n",
                "requester_id": "n",
                "entity_id": "s",
                "due_at": "n",
                "turn": {"oneOf": [ref("TaskWait"), {"type": "null"}]},
                "late": {"oneOf": [obj({"since": "s", "level": {"enum": ["told", "person"]}}), {"type": "null"}]},
                "stuck": {
                    "type": "boolean",
                    "description": "Present and true when the never-stuck ladder brought it to a person (stuck or late)",
                },
                "snoozed": "b",
                "pushed_past": "b",
                "stage_since": "n",
                "on_person": "b",
                "visit_id": {"type": "string", "description": "Its open visit, only when its turn is an ask"},
            },
            [
                "id",
                "number",
                "title",
                "status",
                "version",
                "pipeline_id",
                "stage_id",
                "owner_id",
                "assignee_id",
                "requester_id",
                "due_at",
                "turn",
                "late",
                "snoozed",
                "pushed_past",
                "stage_since",
                "on_person",
            ],
            blocked_by=items(obj({"id": "n", "number": {"type": ["integer", "null"]}})),
            blocks=items(obj({"id": "s", "number": {"type": ["integer", "null"]}})),
        ),
        # What the Assistant did for you, the Needs you summary and why an item is here (docs/assistant.md).
        "NeedsSummary": obj(
            {
                "actor": "s",
                "total": "i",
                "this_week": "i",
                "by_kind": "o",
                "by_bot": items({"type": "object"}),
                "bots_left": "i",
            }
        ),
        "Origin": obj(
            {
                "ref": "s",
                "kind": "s",
                "id": "s",
                "title": "s",
                "created_at": "s",
                "made_by": "o",
                "task": {"type": ["string", "null"]},
                "cause": "o",
                "siblings": "o",
                "stop": items({"type": "object"}),
            }
        ),
    }
)
