# pyright: strict
"""The answers of the Email routes (backend/openapi_v2.py gathers them)."""

from ..api_contract import ref
from ._base import SCHEMAS, items, obj

MAIL_ORGANIZE_ACTION = {"enum": ["archive", "unarchive", "mark_read", "mark_unread", "label", "unlabel"]}


MAIL_ORGANIZE_IDS = {
    "type": "array",
    "items": {"type": "string", "minLength": 1, "maxLength": 128},
    "minItems": 1,
    "maxItems": 500,
}


MAIL_ORGANIZE_FIELDS = {
    "mailbox": {"type": "string", "maxLength": 254},
    "action": MAIL_ORGANIZE_ACTION,
    "ids": MAIL_ORGANIZE_IDS,
    "query": {"type": "string", "minLength": 1, "maxLength": 4096},
    "label": {"type": "string", "minLength": 1, "maxLength": 225},
    "limit": {"type": "integer", "minimum": 1, "maximum": 500, "default": 100},
}


SCHEMAS["MailOrganizeBody"] = {
    "oneOf": [
        {**obj(MAIL_ORGANIZE_FIELDS, required=("mailbox", "action", "ids")), "not": {"required": ["query"]}},
        {**obj(MAIL_ORGANIZE_FIELDS, required=("mailbox", "action", "query")), "not": {"required": ["ids"]}},
    ]
}

SCHEMAS["MailOrganizeUndo"] = obj(
    {"action": MAIL_ORGANIZE_ACTION, "ids": items("s"), "label": "s"}, required=("action", "ids")
)

SCHEMAS["MailOrganizeResult"] = obj(
    {
        "mailbox": "s",
        "action": MAIL_ORGANIZE_ACTION,
        "label": "s",
        "count": "i",
        "ids": items("s"),
        "undo": ref("MailOrganizeUndo"),
    },
    required=("mailbox", "action", "count", "ids", "undo"),
)
