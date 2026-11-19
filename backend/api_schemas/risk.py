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
                "waits": {"type": "boolean", "description": "An open matter waits on the caller (their Needs you)"},
                "has_rules": {"type": "boolean", "description": "An active rule is linked"},
                "open": "i",
                "closed": "i",
                "claimed": items(obj({"currency": "s", "amount": "i"})),
                "next_date": {
                    "type": ["string", "null"],
                    "description": "The soonest obligation still due (YYYY-MM-DD), from the dates the reader sees",
                },
                "unconfirmed": {"type": "integer", "description": "Occurrences of its dates that are unconfirmed"},
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
                "waits",
                "has_rules",
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
        "RiskObligation": obj(
            {
                "id": {"type": "string", "description": "The due date's id (POST /api/v2/rule-due/{id}/satisfied)"},
                "due_on": {"type": "string", "description": "This occurrence's day, YYYY-MM-DD"},
                "title": "s",
                "basis": {"enum": ["", "law", "order", "agency", "contract", "ours", "asked", "theirs"]},
                "state": {
                    "enum": ["due", "met", "waived", "missed", "passed", "unconfirmed", None],
                    "description": "null: theirs to meet and past; we only watch it",
                },
                "group": {"enum": ["unconfirmed", "next", "later", "past"]},
                "repeats": "s",
                "first_due_on": "s",
                "timezone": "s",
                "owner": "s",
                "routine_id": "n",
                "rule": obj({"id": "s", "kind": "s", "text": "s", "source": {"type": ["object", "null"]}}),
                "task": {
                    "oneOf": [obj({"id": "s", "label": "n", "title": "n", "status": "s"}), {"type": "null"}],
                    "description": "The matter the date belongs to",
                },
                "risks": items(obj({"id": "s", "key": "s", "label": "s"})),
                "mark": {
                    "oneOf": [
                        obj(
                            {
                                "satisfied_by": "s",
                                "via": "s",
                                "satisfied_at": "s",
                                "task_id": "n",
                                "outcome": {"enum": ["met", "waived", "missed"]},
                                "proof": {
                                    "oneOf": [obj({"kind": "s", "ref": "n", "name": "n"}), {"type": "null"}],
                                    "description": "ref and name are null for a file or email the reader cannot open",
                                },
                                "note": "s",
                                "confirmed_by_id": "n",
                                "confirmed_at": "n",
                            }
                        ),
                        {"type": "null"},
                    ]
                },
                "can_edit": "b",
                "can_satisfy": "b",
                "can_waive": "b",
            },
            required=("id", "due_on", "title", "basis", "state", "group", "rule", "risks", "mark"),
        ),
        "RiskObligationList": obj({"obligations": items(ref("RiskObligation")), "next_offset": INT_N}),
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
                "obligations": items(ref("RiskObligation")),
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
