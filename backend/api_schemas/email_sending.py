# pyright: strict
"""The answers of the Email sending routes (backend/openapi_v2.py gathers them)."""

from ..api_contract import ref
from ._base import SCHEMAS, items, obj

SCHEMAS.update(
    {
        "EmailSendingPolicy": obj({"company": "o", "inbox": "o", "bot": "o", "effective": "o"}),
        "EmailSend": obj(
            {
                "id": "s",
                "mailbox": "s",
                "actor": "s",
                "bot": "s",
                "via": "s",
                "automation_action_id": "n",
                "calendar_action_id": "n",
                "content_sha256": "s",
                "body_sha256": "s",
                "subject": "s",
                "approval_id": "s",
                "task_id": "n",
                "approval_task_id": "n",
                "state": "s",
                "created_at": "s",
                "send_after": "s",
                "claimed_at": "n",
                "finished_at": "n",
                "provider_message_id": "n",
                "detail": "s",
                "recipients": items("s"),
                "attachments": items("o"),
                "decision": "o",
                "classification": "o",
            }
        ),
        "EmailSendResult": obj({"send": ref("EmailSend")}),
        "EmailSendDetail": obj({"send": ref("EmailSend"), "body": "s", "bodies": items("o"), "preview_complete": "b"}),
        "EmailSendList": obj({"sends": items(ref("EmailSend")), "next_cursor": "n"}),
        "EmailSuppressionList": obj({"recipients": items("o"), "next_cursor": "n"}),
    }
)
