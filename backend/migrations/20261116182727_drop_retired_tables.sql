-- Tables only code for databases below the compatibility floor (v0.6.0-rc.1) used; nothing reads or writes them now.
-- cloud_migrations: Store.initialize's guarded "cloud" steps recorded themselves here, and every database at the floor
-- ran all 77 of them.
DROP TABLE IF EXISTS cloud_migrations;
-- mail_private_backfill: the bill and tax letters an older postal mail step listed for making private, which the next
-- start emptied.
DROP TABLE IF EXISTS mail_private_backfill;
