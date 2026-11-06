"""Email sending (backend/email_sending.py): the send policy, sends, previews, operations and suppressions.

Rows come from `sqlite3.Row` (SELECT *); see `backend/rows.py` for the rules."""

from typing import TypedDict


class EmailSendingPolicyRow(TypedDict):
    scope: str
    settings: str
    updated_at: str
    updated_by: str


class EmailSendRow(TypedDict):
    id: str
    mailbox: str
    actor: str
    bot: str
    via: str
    automation_action_id: str | None
    content_sha256: str
    idempotency_key: str
    recipients: str
    attachments: str
    subject: str
    body_sha256: str
    raw: str
    draft_id: str
    thread_id: str
    reply_to_id: str
    approval_id: str
    classification: str
    decision: str
    state: str
    created_at: str
    send_after: str
    claimed_at: str | None
    provider_attempted_at: str | None
    runner_id: str | None
    finished_at: str | None
    provider_message_id: str | None
    detail: str
    operation_key: str
    lease_id: str
    checked_at: str
    calendar_action_id: str | None


class EmailSuppressedRecipientRow(TypedDict):
    mailbox: str
    address: str
    reason: str
    created_at: str


class EmailLegacyAutomationRow(TypedDict):
    automation_id: str
    owner_id: str
    version: int


class EmailSendPreviewRow(TypedDict):
    send_id: str
    actor: str
    content_sha256: str
    shown_at: str


class EmailSendOperationRow(TypedDict):
    actor: str
    operation_key: str
    send_id: str


TABLES = {
    EmailSendingPolicyRow: "email_sending_policies",
    EmailSendRow: "email_sends",
    EmailSuppressedRecipientRow: "email_suppressed_recipients",
    EmailLegacyAutomationRow: "email_legacy_automations",
    EmailSendPreviewRow: "email_send_previews",
    EmailSendOperationRow: "email_send_operations",
}
