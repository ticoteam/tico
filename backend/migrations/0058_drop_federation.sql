-- Federation is removed: installs no longer peer, share threads or relay envelopes through Tico HQ. Its tables are the ones
-- migration 0047 made in its federation section (backend/migrations/0047_native_threads_schema.py, which stays as it ran).
-- Peerings, keys, shared-thread state and queued envelopes go with them: no data is carried over. Dropping a table drops
-- its indexes. The unsent index on message_deliveries served only the federation "not sent to another company" check.
-- Thread messages a shared thread brought in stay as thread history; nothing reads them as federation any more.
DROP INDEX IF EXISTS message_deliveries_unsent;
DROP TABLE IF EXISTS federation_deferrals;
DROP TABLE IF EXISTS federation_outbox_items;
DROP TABLE IF EXISTS federation_receipts;
DROP TABLE IF EXISTS federation_messages;
DROP TABLE IF EXISTS federation_items;
DROP TABLE IF EXISTS federation_members;
DROP TABLE IF EXISTS federation_threads;
DROP TABLE IF EXISTS federation_directory_entries;
DROP TABLE IF EXISTS federation_peerings;
DROP TABLE IF EXISTS federation_keys;
DROP TABLE IF EXISTS federation_states;
