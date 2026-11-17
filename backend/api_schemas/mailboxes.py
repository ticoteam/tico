# pyright: strict
"""The answers of the Mailboxes routes (backend/openapi_v2.py gathers them)."""

from ..api_contract import ref
from ._base import SCHEMAS, items, obj

_MAILBOX_LEVEL = {"enum": ["none", "read", "read_write"]}


_MAILBOX_REF = obj({"id": "s", "name": "s"})


SCHEMAS.update(
    {
        "Mailbox": obj(
            {
                "id": "s",
                "address": "s",
                "provider": "s",
                "kind": {"enum": ["personal", "shared"]},
                "person_id": "n",
                "person_name": "s",
                "connection": {"enum": ["workspace", "personal"]},
                "status": {"enum": ["active", "stopped"]},
                "state": {"enum": ["connected", "not_connected", "awaiting_person", "stopped"]},
                "level": _MAILBOX_LEVEL,
                "routines": items(_MAILBOX_REF),
                "used_by": obj({"people": "i", "bots": "i", "names": items({"type": "string"})}),
                "synced_at": "n",
                "newest_epoch": {"type": ["integer", "null"]},
                "sync": {"enum": ["", "signin", "failed"]},
                "sync_error": "s",
            }
        ),
        "MailboxMay": obj({"read": "b", "organize": "b", "send": "b", "change_calendar": "b"}),
        "MailboxList": obj(
            {
                "admin": "b",
                "domain": "s",
                "may": ref("MailboxMay"),
                "mine": {"anyOf": [ref("Mailbox"), {"type": "null"}]},
                "shared": items(ref("Mailbox")),
                "mailboxes": items(ref("Mailbox")),
            }
        ),
        "MailboxResult": obj({"mailbox": ref("Mailbox")}),
        "MailboxDetail": obj(
            {
                "mailbox": ref("Mailbox"),
                "can_manage": "b",
                "can_stop": "b",
                "accesses": items(
                    obj(
                        {
                            "id": "n",
                            "grantee_type": {"enum": ["person", "bot", "admins"]},
                            "grantee_id": "s",
                            "name": "s",
                            "level": {"enum": ["read", "read_write"]},
                            "implicit": "b",
                        }
                    )
                ),
                "routines": items(obj({"id": "s", "name": "s", "enabled": "b", "can_edit": "b"})),
                "may": ref("MailboxMay"),
                "candidates": items(obj({"type": {"enum": ["person", "bot"]}, "id": "s", "name": "s"})),
            }
        ),
    }
)
