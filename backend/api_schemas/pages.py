# pyright: strict
"""The answers of the Pages routes (backend/openapi_v2.py gathers them)."""

from ..api_contract import ref
from ._base import SCHEMAS, items, obj

SCHEMAS.update(
    {
        # Pages and Library › Files (backend/pages.py, backend/file_library.py).
        "Page": obj(
            {
                "audience": {
                    "anyOf": [ref("AccessAudience"), {"type": "null"}],
                    "description": "Only returned to managers on a single page read or write; null inherits where it is filed",
                },
                "restricted": {
                    "type": "boolean",
                    "description": "Only returned to managers, including on lists and cards",
                },
                "file_id": "s",
                "title": "s",
                "bot_id": "s",
                "bot_name": "s",
                "version": "i",
                "updated_at": "s",
                "updated_by": "n",
                "updated_by_name": "s",
                "cadence": {"type": "string", "enum": ["none", "daily", "weekly", "monthly"]},
                "as_of": {"type": ["string", "null"], "description": "The date the data is from (YYYY-MM-DD)"},
                "stale_days": {
                    "type": ["integer", "null"],
                    "description": "Days since the last version once past "
                    "1.5x the cadence; null while current or without a cadence",
                },
                "replaced_by_id": {
                    "type": ["string", "null"],
                    "description": "The newer page's file id, when the reader may see it",
                },
                "replaced_by_title": "n",
                "archived": "b",
                "task_id": "n",
                "src": {
                    "type": ["string", "null"],
                    "description": "Where to frame the page; null means open it "
                    "(GET /api/v2/files/{fid}/versions/{number})",
                },
            },
            required=[
                "file_id",
                "title",
                "bot_id",
                "bot_name",
                "version",
                "updated_at",
                "updated_by",
                "cadence",
                "as_of",
                "stale_days",
                "replaced_by_id",
                "archived",
                "task_id",
                "src",
            ],
        ),
        "PageShown": obj(
            {
                "page": ref("Page"),
                "can_manage": "b",
                "can_delete": "b",
                "pinned": {
                    "type": "array",
                    "description": "The places it is pinned that the caller may open",
                    "items": obj({"place": "s", "label": "s"}),
                },
            },
            required=["page"],
        ),
        "PagePublished": obj(
            {"page": ref("Page"), "unembedded": items({"type": "string"}), "created": "b"},
            required=["page", "unembedded"],
        ),
        "PageList": obj({"pages": items(ref("Page")), "has_more": "b", "next_cursor": "n"}, required=["pages"]),
    }
)
