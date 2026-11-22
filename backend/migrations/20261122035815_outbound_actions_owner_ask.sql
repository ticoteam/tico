-- The owner's ask about an outbound action whose outcome stays unknown (backend/owner_asks.py): the requirement id of
-- that one ask, '' for none. Withdrawn when the row settles (docs/outbound-actions.md "Unknown").
ALTER TABLE outbound_actions ADD COLUMN owner_ask_id TEXT NOT NULL DEFAULT '';
