# pyright: strict
"""The answers of the Page history routes (backend/page_history.py; backend/openapi_v2.py gathers them)."""

from ..api_contract import ref
from ._base import SCHEMAS, items, obj

SCHEMAS.update(
    {
        "PageVisit": obj(
            {
                "id": "s",
                "page": {"type": "string", "description": "The app's address path, without the search"},
                "title": {"type": "string", "description": "The one line the app shows for the page"},
                "subject_type": {"type": ["string", "null"], "description": "The kind of record the page shows"},
                "subject_id": "n",
                "via": {
                    "type": "string",
                    "enum": ["", "assistant"],
                    "description": "assistant when Tico opened the page for the person",
                },
                "created_at": "s",
            }
        ),
        "PageVisitList": obj(
            {
                "visits": items(ref("PageVisit")),
                "next": {"type": ["string", "null"], "description": "Pass as `before` to read older visits"},
            }
        ),
        "PageVisitResult": obj({"visit": ref("PageVisit")}),
        "PageHistoryCleared": obj({"cleared": {"type": "integer", "description": "How many visits were removed"}}),
    }
)
