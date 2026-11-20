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
                "draft_id": "s",
                "thread_id": "n",
                "reply_to_id": "n",
                "recipients": items("s"),
                "attachments": items("o"),
                "decision": "o",
                "classification": "o",
            }
        ),
        "EmailSendResult": obj(
            # `draft` on a cancel of a send frozen from a draft: the draft is kept in Gmail.
            {"send": ref("EmailSend"), "draft": obj({"id": "s", "kept": "b", "detail": "s"})},
            required=("send",),
        ),
        "EmailSendDetail": obj(
            {
                "send": ref("EmailSend"),
                "body": "s",
                "bodies": items("o"),
                "preview_complete": "b",
                # The frozen copy's To, Cc and Bcc apart (send.recipients is all of them); empty once it is gone.
                "to": items("s"),
                "cc": items("s"),
                "bcc": items("s"),
                # What the OK sends back (POST /api/v2/tasks/{tid}/asks/{rid}/ok); null when the send has no OK to give.
                "shown_hash": "n",
            }
        ),
        "EmailSendList": obj({"sends": items(ref("EmailSend")), "next_cursor": "n"}),
        "EmailSuppressionList": obj({"recipients": items("o"), "next_cursor": "n"}),
    }
)
