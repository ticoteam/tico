# pyright: strict
"""The answers of the Updates routes (backend/openapi_v2.py gathers them)."""

from ._base import SCHEMAS, obj

SCHEMAS.update(
    {
        "UpdateList": obj({"updates": "a", "unread": "i", "next_before": "n"}, required=["updates", "unread"]),
        "UpdateArchiveResult": obj(
            {"updated": "i", "archived": "b", "unread": "i"}, required=["updated", "archived", "unread"]
        ),
        "Unread": obj(
            {"unread": "i"},
            meetings_pending={
                "type": "integer",
                "description": "Pending meetings filed for the caller; never part of Needs you",
            },
        ),
    }
)
