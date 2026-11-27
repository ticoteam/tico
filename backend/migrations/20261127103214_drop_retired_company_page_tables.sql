-- The Company page is retired (C-2, shipped in v0.6.337) and nothing reads its tables any more. They still held
-- personal data (its readers' addresses, sessions, invites and messages), so they go, with their indexes.
-- Children first, so the drops never meet a foreign key.
DROP TABLE IF EXISTS reader_notices;
DROP TABLE IF EXISTS reader_messages;
DROP TABLE IF EXISTS edition_views;
DROP TABLE IF EXISTS reader_invites;
DROP TABLE IF EXISTS reader_sessions;
DROP TABLE IF EXISTS audience_readers;
DROP TABLE IF EXISTS readers;
DROP TABLE IF EXISTS edition_checks;
DROP TABLE IF EXISTS edition_sources;
DROP TABLE IF EXISTS editions;
DROP TABLE IF EXISTS stat_board_approvals;
DROP TABLE IF EXISTS disclosure_rules;
DROP TABLE IF EXISTS disclosure_revisions;
DROP TABLE IF EXISTS audience_sources;
DROP TABLE IF EXISTS audiences;
DROP TABLE IF EXISTS page_stats;
DROP TABLE IF EXISTS page_releases;
DROP TABLE IF EXISTS company_page_settings;
