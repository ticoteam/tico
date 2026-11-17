# pyright: strict
"""The answers of the Feedback routes (backend/openapi_v2.py gathers them)."""

from ..api_contract import ref
from ._base import SCHEMAS, items, obj

SCHEMAS.update(
    {
        "Feedback": obj(
            {
                "id": "s",
                "person_id": "s",
                "subject_type": {"enum": ["message", "task", "approval", "ask"]},
                "subject_id": "s",
                "bot_id": "s",
                "rating": {"enum": ["up", "down"]},
                "reason": "s",
                "outcome": {"enum": ["none", "told_bot", "rule"]},
                "note_id": "n",
                "note_status": {"enum": ["waiting", "carried", "settled", "canceled", None]},
                "org_rule_id": "n",
                "rule_status": {"enum": ["suggested", "active", "retired", None]},
                "created_at": "s",
                "updated_at": "s",
            }
        ),
        "FeedbackMarkers": obj(
            {"items": items(ref("Feedback")), "counts": items(obj({"subject_id": "s", "up": "i", "down": "i"}))}
        ),
        "FeedbackPage": obj({"items": items(ref("Feedback")), "next_cursor": "n"}),
        "FeedbackReasons": obj({"reasons": items(obj({"text": "s", "count": "i"}))}),
        "FeedbackRemoved": obj({"ok": "b", "id": "s"}),
        "FeedbackRuleDraft": obj({"text": "s", "drafted": "b"}),
        "FeedbackRuleMade": obj({"feedback": ref("Feedback"), "rule": "o"}),
    }
)
