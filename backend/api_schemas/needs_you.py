# pyright: strict
"""The answers of the Needs you routes (backend/openapi_v2.py gathers them)."""

from ..api_contract import ref
from ._base import SCHEMAS, items, obj

# One item of a person's Needs you (backend/views.py needs_items).
NEEDS_ITEM = obj(
    {
        "id": "s",
        "kind": {
            "type": "string",
            "enum": ["task", "ask", "paused", "mention", "question"],
            "description": "task: a private task they must let someone see (`task.turn.share`); ask: a requirement "
            "with their name (its fields in "
            "`ask`); paused: a bot that cannot run, which they can fix; mention: an @ of them by a person they have "
            "not replied to; question: a bot's ❓ on its message for them, until they reply in that chat",
        },
        "line": "s",
        "since": "n",
        "task": {"oneOf": [ref("TaskBrief"), {"type": "null"}], "description": "The brief task it is about"},
    },
    required=["id", "kind", "line", "since"],
    ask={"oneOf": [ref("TaskRequirement"), {"type": "null"}]},
    task_id={"type": ["string", "null"]},
    requirement_id={"type": ["string", "null"]},
    message_id={"type": "string", "description": "A mention's or a ❓'s message"},
    mark_id={"type": "string", "description": "A ❓'s mark (POST /api/v2/questions/{mid}/give)"},
    shows={
        "type": "array",
        "items": {"type": "string"},
        "description": "A ❓'s website browser to take over (`browser:<tool>/<account>`)",
    },
    from_id={"type": "string", "description": "Who wrote it: a mention's person, a ❓'s bot"},
    conversation_id={"type": "string", "description": "The thread a mention was written in"},
    root_id={"type": "string", "description": "A mention's root thread: the rail row it moves"},
    href={"type": "string", "description": "A paused cause a person fixes in the app: where (Connect GitHub)"},
    snoozed_until={
        "type": "string",
        "description": "In GET /api/v2/needs-you/snoozed: when this snoozed ask comes back",
    },
    about={
        "type": "string",
        "description": "An OK whose task is titled by its kind alone (\"Mail a letter\"): what this one is about, "
        "in its subject's words (\"Mail a letter to Ana Diaz\"), for its party; the row's first line",
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
                obj({"actor": "s", "count": "i"}),
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
