-- Each operation id is bound to the request that queued its send (backend/email_sending.py `remember`): the same
-- request again finds its send, whatever a revision of its draft froze since; another request under that id is refused.
-- Receipts written before this have none ('') and are matched by the content their send froze, as before.
ALTER TABLE email_send_operations ADD COLUMN request_sha256 TEXT NOT NULL DEFAULT '';
