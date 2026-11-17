# pyright: strict
"""The answers of the Events routes (backend/openapi_v2.py gathers them)."""

from ..api_contract import ref
from ._base import SCHEMAS, items, obj

SCHEMAS.update(
    {
        "EventType": obj(
            {
                "name": "s",
                "group": "s",
                "description": "s",
                "label": "s",
                "version": "i",
                "example": "o",
                "aliases": items({"type": "string"}),
                "sourceless": "b",
                "outbound": "b",
            },
            required=["name", "group", "description", "label", "version", "example"],
        ),
        "EventTypeList": obj({"items": items(ref("EventType"))}, required=["items"]),
    }
)
