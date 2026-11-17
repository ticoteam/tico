# pyright: strict
"""The answers of the Changelog routes (backend/openapi_v2.py gathers them)."""

from ..api_contract import ref
from ._base import SCHEMAS, items, obj

SCHEMAS.update(
    {
        "ChangelogEntry": obj(
            {
                "id": "s",
                "title": "s",
                "bullets": items({"type": "string"}),
                "area": "s",
                "kind": {"type": "string", "enum": ["product", "activity"]},
                "shipped_at": "s",
                "published_at": "s",
                "unread": "b",
                "version": "s",
                "source": "s",
                "url": "s",
                "task_id": "s",
            },
            required=["id", "title", "bullets", "area", "kind", "shipped_at"],
        ),
        "ChangelogList": obj(
            {
                "entries": items(ref("ChangelogEntry")),
                "unread_count": "i",
                "current_version": "s",
                "next_offset": {"type": ["integer", "null"]},
                "drafts": "a",
                "can_review": "b",
                "can_add": "b",
            }
        ),
        "ChangelogReadResult": obj({"unread_count": "i"}),
    }
)
