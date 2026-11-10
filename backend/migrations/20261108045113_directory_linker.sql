-- The linker's tables and indexes (track 6b, spec §7.1). It stands alone: the entity tables come from the directory
-- tables migration, and message_revisions and messages from the threads schema.
--
-- entity_lookups is the run trace (step 1): the entities a bot's attempt read through `hub directory get` or `rules`.
-- The runner's tool events keep a command's path, never its arguments, so the ids are recorded here, once per attempt
-- and entity.
CREATE TABLE IF NOT EXISTS entity_lookups (
  id TEXT PRIMARY KEY,
  attempt_id TEXT NOT NULL,
  entity_id TEXT NOT NULL REFERENCES entities(id),
  created_at TEXT NOT NULL,
  UNIQUE (attempt_id, entity_id));
CREATE INDEX IF NOT EXISTS entity_lookups_attempt ON entity_lookups(attempt_id);
-- The linker reads edits in the order they were made, by (created_at, id); message_revisions had no such index.
CREATE INDEX IF NOT EXISTS message_revisions_created ON message_revisions(created_at, id);
