# pyright: strict
"""The answers of the Live events routes (backend/openapi_v2.py gathers them)."""

from ..api_contract import ref
from ._base import SCHEMAS, obj

SCHEMAS.update(
    {
        "TaskEvent": obj(
            {
                "seq": "i",
                "actor": "n",
                "at": "s",
                "id": "s",
                "kind": {"type": "string", "description": "The last change kind for this task in the batch"},
                "kinds": {
                    "type": "array",
                    "items": {"type": "string"},
                    "uniqueItems": True,
                    "description": "All change kinds for this task in the batch; files invalidates files and linked pages",
                },
                "task": ref("Task"),
                "was": {
                    "type": "object",
                    "properties": {"owner": {"type": ["string", "null"]}, "status": {"type": ["string", "null"]}},
                    "description": "The task's owner and status before this batch moved them, only to a viewer who could "
                    "read the task then and can now (owner null where the viewer may not see it)",
                },
                "gone": "b",
            },
            required=["seq", "actor", "at", "id", "kind", "kinds"],
        ),
    }
)
