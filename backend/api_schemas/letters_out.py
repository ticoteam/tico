# pyright: strict
"""The answers of the Letters out routes (backend/openapi_v2.py gathers them)."""

from ..api_contract import ref
from ._base import SCHEMAS, items, obj

SCHEMAS["LetterOutContents"] = obj(
    {
        **{
            key: "s"
            for key in ("id", "requested_by", "mail_class", "currency", "status", "error", "created_at", "updated_at")
        },
        **{
            key: "n"
            for key in (
                "acting_bot_id",
                "automation_id",
                "task_id",
                "thread_id",
                "quote_id",
                "quote_expires_at",
                "approval_id",
                "approval_hash",
                "hq_letter_id",
                "proof",
            )
        },
        "recipient": "o",
        "return_address": "o",
        "tracking": items(obj({"barcode": "s", "status": "n"})),
        "page_count": {"type": ["integer", "null"]},
        "total_micros": "i",
        "color": "b",
        "duplex": "b",
        "cancel_possible": "b",
        "letter_id": "s",
        "approval_reason": "s",
        "accepted_at": "n",
        "billing": "s",
        "charged_micros": {"type": ["integer", "null"]},
        "redacted": {"enum": [False]},
        "can_cancel": "b",
    }
)

SCHEMAS["LetterOutMetadata"] = obj(
    {
        **{key: "s" for key in ("id", "mail_class", "currency", "status", "billing", "created_at", "updated_at")},
        "acting_bot_id": "n",
        "page_count": {"type": ["integer", "null"]},
        "total_micros": "i",
        "charged_micros": {"type": ["integer", "null"]},
        "redacted": {"enum": [True]},
        "can_cancel": "b",
    }
)

SCHEMAS["LetterOutMetadata"]["additionalProperties"] = False

SCHEMAS["LetterOut"] = {"oneOf": [ref("LetterOutContents"), ref("LetterOutMetadata")]}

SCHEMAS["LetterOutPage"] = obj({"items": items(ref("LetterOut")), "next_before": "n"})

SCHEMAS["LettersSettings"] = obj(
    {
        "id": "s",
        "mode": "s",
        "return_address": {"type": ["object", "null"]},
        "monthly_cap_micros": {"type": ["integer", "null"]},
        "availability": "s",
        "checked_at": "n",
        "created_at": "s",
        "updated_at": "s",
    }
)

SCHEMAS["LettersAccess"] = obj({"send": "b", "outbox_id": "s"})

SCHEMAS["LettersGrants"] = obj(
    {
        "outbox_id": "s",
        "next": "n",
        "grants": items(
            obj(
                {
                    "id": "s",
                    "outbox_id": "s",
                    "grantee_type": "s",
                    "grantee_id": "s",
                    "level": "s",
                    "granted_by_id": "s",
                    "created_at": "s",
                }
            )
        ),
    }
)

SCHEMAS["LettersGrant"] = obj({"grantee_type": "s", "grantee_id": "s", "level": "s"})

SCHEMAS["LettersRevoked"] = obj({"revoked": "b"})

SCHEMAS["LetterBotSettings"] = obj({"bot_id": "s", "mode": "n", "monthly_cap_micros": {"type": ["integer", "null"]}})
