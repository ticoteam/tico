-- The replies to an update (updates.thread) and the feed's reply counts (updates.listing) scanned every message.
-- Partial, so chat without refs.update adds no entry. Startup code made it on databases that ran a later release;
-- one still at v0.6.0-rc.1 gets it here.
CREATE INDEX IF NOT EXISTS messages_refs_update ON messages(json_extract(refs,'$.update'))
WHERE json_extract(refs,'$.update') IS NOT NULL;
