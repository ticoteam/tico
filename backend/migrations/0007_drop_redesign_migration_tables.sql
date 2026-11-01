-- The redesign cutover's record table (its state is in registry_metadata) and the accepts table nothing writes.
-- redesign_migrated, which redesign_migration.holds reads, stays.
DROP TABLE IF EXISTS redesign_migrations;
DROP TABLE IF EXISTS redesign_migration_accepts;
