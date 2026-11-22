-- The owner's ask about an outbound action whose outcome stays unknown (backend/owner_asks.py): the requirement id of
-- that one ask, '' for none, withdrawn when the row settles; and when its outcome was last looked up at the provider,
-- so an unknown row is looked up hourly after the first look (docs/outbound-actions.md "Unknown").
ALTER TABLE outbound_actions ADD COLUMN owner_ask_id TEXT NOT NULL DEFAULT '';
ALTER TABLE outbound_actions ADD COLUMN reconciled_at TEXT;
