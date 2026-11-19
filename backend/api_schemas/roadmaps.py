# pyright: strict
"""The answers of the Roadmaps routes (backend/roadmaps.py, backend/public_roadmaps.py; openapi_v2 gathers them)."""

from typing import Any

from ..api_contract import ref
from ._base import SCHEMAS, items, obj

NEXT_OFFSET: dict[str, Any] = {"type": ["integer", "null"], "description": "Pass back as offset; null on the last page"}
# A task's requester tallies (roadmaps.STATS_COLUMNS): the same on a task's requester list and on a ranked row.
TALLIES: dict[str, Any] = {
    "requester_count": "i",
    "external_requesters": {"type": "integer", "description": "Requesters outside the team (no actor)"},
    "internal_requesters": {"type": "integer", "description": "Teammates, a person or a bot"},
    "important_requesters": "i",
    "organization": {
        "type": ["string", "null"],
        "description": "The first organization a requester gave, alphabetically; null when none gave one",
    },
}

SCHEMAS.update(
    {
        # One row of the Roadmap: a Product pipeline task with its requester tallies.
        "RoadmapTask": obj(
            {
                "id": "s",
                "number": {"type": ["integer", "null"]},
                "title": "s",
                "status": {"enum": ["open", "done", "closed"]},
                "visibility": {"enum": ["public", "private"]},
                "public_title": {
                    "type": "string",
                    "description": "The text a public roadmap shows; empty until written",
                },
                "size": {"type": ["number", "null"]},
                "version": "i",
                "pipeline_id": "s",
                "stage_id": "n",
                "stage_name": "n",
                **TALLIES,
            }
        ),
        "RoadmapTaskList": obj({"tasks": items(ref("RoadmapTask")), "next_offset": NEXT_OFFSET}),
        "TaskRequester": obj(
            {
                "id": "s",
                "kind": {"enum": ["teammate", "external"]},
                "actor": {"type": ["string", "null"], "description": "A teammate's actor id; null for an outsider"},
                "name": "s",
                "email": "s",
                "phone": "s",
                "other": "s",
                "organization": "s",
                "note": "s",
                "source": {"enum": ["manual", "public_form", "inbox", "meeting"]},
                "important": {"type": "integer", "enum": [0, 1], "description": "1 for an important requester"},
                "created_at": "s",
            }
        ),
        "TaskRequesterList": obj(
            {
                "requesters": items(ref("TaskRequester")),
                "next_offset": NEXT_OFFSET,
                "my_requester_id": {
                    "type": ["string", "null"],
                    "description": "The caller's own request on this task, on any page; null when they made none",
                },
                **TALLIES,
            }
        ),
        # A person's write answers so; BotOps acting for a person answers its proposal card instead, without these.
        "TaskRequesterAdded": obj(
            {"id": "s", "requester_token": {"type": "string", "description": "Removes this request; shown once"}},
            required=[],
        ),
        "TaskRequesterUpdated": obj({"updated": "b"}, required=[]),
        "TaskRequesterRemoved": obj({"removed": "b"}, required=[]),
        "TaskCommentModerated": obj({"moderated": "b"}),
        "RoadmapSettingsView": obj(
            {
                "labels": items("s")
                | {"description": "Tag keys: /public/roadmaps/{label} is a public roadmap of the tasks with one"},
                "cors_origins": items("s"),
                "action_limit": {"type": "integer", "description": "Requests per visitor per day"},
                "suggestion_limit": {"type": "integer", "description": "Visitor suggestions waiting for review"},
                "bot_public_approval": "b",
                "suggestions_bot": {"type": ["string", "null"], "description": "The chosen bot; null is Automatic"},
                "suggestions_receiver": obj(
                    {
                        "bot": "n",
                        "name": "n",
                        "source": {"enum": ["chosen", "automatic", "chosen_archived", "chosen_inactive"]},
                    }
                )
                | {"description": "Who visitor suggestions go to now, and why"},
            }
        ),
    }
)
