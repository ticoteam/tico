"""Letters and postal mail (backend/letters.py, backend/postal_mail.py).

Rows come from `sqlite3.Row` (SELECT *); see `backend/rows.py` for the rules."""

from typing import TypedDict


class PostalMailGrantRow(TypedDict):
    id: str
    address_id: str
    grantee_type: str
    grantee_id: str
    level: str
    granted_by_id: str
    created_at: str
    updated_at: str


class PostalMailSettingsRow(TypedDict):
    id: int
    legal_bot_id: str | None
    updated_by_id: str | None
    created_at: str
    updated_at: str


class LetterOutboxRow(TypedDict):
    id: str
    mode: str
    return_address: str | None
    monthly_cap_micros: int | None
    availability: str
    checked_at: str | None
    created_at: str
    updated_at: str


class LetterOutboxGrantRow(TypedDict):
    id: str
    outbox_id: str
    grantee_type: str
    grantee_id: str
    level: str
    granted_by_id: str
    created_at: str
    updated_at: str


class LetterBotSettingRow(TypedDict):
    bot_id: str
    mode: str | None
    monthly_cap_micros: int | None
    updated_by: str
    updated_at: str


class LetterRow(TypedDict):
    id: str
    outbox_id: str
    requested_by: str
    acting_bot_id: str | None
    automation_id: str | None
    task_id: str | None
    thread_id: str | None
    attempt_id: str | None
    recipient: str
    recipient_hash: str
    return_address: str
    mail_class: str
    color: int
    duplex: int
    source_blob_id: str
    original_blob_id: str | None
    source_provenance: str
    proof_blob_id: str | None
    page_count: int | None
    quote_id: str | None
    quote_expires_at: str | None
    approval_hash: str | None
    approval_id: str | None
    approved_hash: str | None
    submission_quote_id: str | None
    hq_letter_id: str | None
    total_micros: int
    currency: str
    status: str
    tracking: str
    cancel_possible: int
    always_ask: int
    approval_reason: str
    error: str
    attempts: int
    next_sync_at: str | None
    submitted_at: str | None
    accepted_at: str | None
    charged_micros: int | None
    spend_released: int
    billing: str
    created_at: str
    updated_at: str


class LetterSourceRow(TypedDict):
    letter_id: str
    source_type: str
    source_id: str


class LetterProofRow(TypedDict):
    letter_id: str
    blob_id: str
    approval_hash: str
    created_at: str


TABLES = {
    PostalMailGrantRow: "postal_mail_grants",
    PostalMailSettingsRow: "postal_mail_settings",
    LetterOutboxRow: "letter_outboxes",
    LetterOutboxGrantRow: "letter_outbox_grants",
    LetterBotSettingRow: "letter_bot_settings",
    LetterRow: "letters",
    LetterSourceRow: "letter_sources",
    LetterProofRow: "letter_proofs",
}
