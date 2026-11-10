-- When an OK was sent back or taken back (backend/task_asks.py `revoke`): its own durable fact, never read from the
-- answers, so undoing the send back never brings the OK back. A revoked grant lets nothing go again.
ALTER TABLE ok_grants ADD COLUMN revoked_at TEXT;
