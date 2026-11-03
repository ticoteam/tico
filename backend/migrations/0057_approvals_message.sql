-- The approval history read (views.history_rows, GET /api/v2/approvals?status=approved|declined) joins a person's messages
-- to their approvals on approvals.message_id. Nothing indexed that column, so each join scanned every approval in the company.
CREATE INDEX IF NOT EXISTS approvals_message ON approvals(message_id);
