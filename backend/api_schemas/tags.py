# pyright: strict
"""The answers of the Tags routes (backend/openapi_v2.py gathers them)."""

from ..api_contract import ref
from ._base import SCHEMAS, items, obj

SCHEMAS.update(
    {
        "Tag": obj(
            {
                "id": "s",
                "key": "s",
                "label": "s",
                "metadata": "o",
                "markdown": "s",
                "is_template": "b",
                "template_id": "n",
                "owner": "n",
                "bot_writers": items("s"),
                "version": "i",
                "created_at": "s",
                "updated_at": "s",
            }
        ),
        "TagList": obj({"tags": items(ref("Tag"))}),
        "TagResult": obj({"tag": ref("Tag")}),
        "TagDetail": obj(
            {
                "tag": ref("Tag"),
                "editable": "b",
                "tasks": items(ref("Task")),
                "next_offset": {"type": ["integer", "null"]},
            }
        ),
    }
)
