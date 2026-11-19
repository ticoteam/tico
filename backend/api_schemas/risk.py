# pyright: strict
"""The answers of the Risk routes (backend/risk.py; backend/openapi_v2.py gathers them)."""

from typing import Any

from ..api_contract import ref
from ._base import SCHEMAS, items, obj

INT_N: dict[str, Any] = {"type": ["integer", "null"]}
NAMED_N: dict[str, Any] = {
    "oneOf": [obj({"id": "s", "name": "s"}), {"type": "null"}],
    "description": "A Directory record, null when the reader may not see it",
}

SCHEMAS.update(
    {
        "Risk": obj(
            {
                "id": "s",
                "key": "s",
                "label": "s",
                "kind": {"enum": ["risk"]},
                "owner": "n",
                "version": "i",
                "bot_writers": items("s"),
                "markdown": {"type": "string", "description": "Omitted in the list"},
                "level": {"enum": ["live", "open", "quiet"]},
                "open": "i",
                "closed": "i",
                "claimed": items(obj({"currency": "s", "amount": "i"})),
                "next_date": "n",
                "created_at": "s",
                "updated_at": "s",
            },
            required=(
                "id",
                "key",
                "label",
                "kind",
                "owner",
                "version",
                "level",
                "open",
                "closed",
                "claimed",
                "next_date",
            ),
        ),
        "Matter": obj(
            {
                "task_id": "s",
                "kind": {"enum": ["dispute", "request", "notice", "incident"]},
                "party_entity_id": "n",
                "party": NAMED_N,
                "amount": INT_N,
                "currency": "n",
                "claim": "s",
                "side": {"enum": ["theirs", "ours"]},
                "forum": "s",
                "place_id": "n",
                "place": NAMED_N,
                "product_id": "n",
                "product": NAMED_N,
                "version": "i",
                "created_by_id": "s",
                "created_at": "s",
                "updated_by_id": "s",
                "updated_at": "s",
            }
        ),
        "MatterRow": obj(
            {
                "task": obj(
                    {
                        "id": "s",
                        "number": INT_N,
                        "label": "n",
                        "title": "n",
                        "status": "s",
                        "due_at": "n",
                        "created_at": "n",
                        "owner_id": "n",
                        "pipeline_id": "n",
                        "stage_id": "n",
                        "stage": "n",
                    },
                    required=("id", "status"),
                ),
                "matter": {"oneOf": [ref("Matter"), {"type": "null"}]},
                "risks": items(obj({"id": "s", "key": "s", "label": "s"})),
            }
        ),
        "RiskLinkRow": obj({"link_id": "s"}),
        "RiskList": obj({"risks": items(ref("Risk"))}),
        "RiskResult": obj({"risk": ref("Risk")}),
        "RiskDetail": obj(
            {
                "risk": ref("Risk"),
                "editable": "b",
                "matters": items(ref("MatterRow")),
                "next_offset": INT_N,
                "rules": items(ref("RiskLinkRow")),
                "sources": items(ref("RiskLinkRow")),
                "coverage": items(ref("RiskLinkRow")),
            }
        ),
        "MatterList": obj({"matters": items(ref("MatterRow")), "next_offset": INT_N}),
        "MatterResult": obj({"task_id": "s", "matter": {"oneOf": [ref("Matter"), {"type": "null"}]}}),
        "RiskLinkResult": obj(
            {
                "link": {"type": ["object", "null"]},
                "removed": {"type": "string", "description": "The unlinked link's id"},
            },
            required=("link",),
        ),
    }
)
