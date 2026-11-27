# pyright: strict
"""The answers of the Roadmaps routes (backend/roadmaps.py; openapi_v2 gathers them): the signed-in Roadmap and a task's
requesters."""

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
                    "description": "The task's public title; empty until written",
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
        # A person's write answers so; Agent Ops acting for a person answers its proposal card instead, without these.
        "TaskRequesterAdded": obj(
            {"id": "s", "requester_token": {"type": "string", "description": "Removes this request; shown once"}},
            required=[],
        ),
        "TaskRequesterUpdated": obj({"updated": "b"}, required=[]),
        "TaskRequesterRemoved": obj({"removed": "b"}, required=[]),
        "TaskCommentModerated": obj({"moderated": "b"}),
    }
)
