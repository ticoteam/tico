"""The Company page's tables; hubdb.MIGRATIONS applies this once. Later changes are new migrations, never edits."""
COMPANY_PAGE_SCHEMA = """
CREATE TABLE IF NOT EXISTS company_page_settings (
  id TEXT PRIMARY KEY CHECK (id = 'company'), transparency TEXT NOT NULL DEFAULT 'closed'
    CHECK (transparency IN ('closed','private','open')),
  slug TEXT, accent TEXT, logo_file_id TEXT, updated_at TEXT, updated_by TEXT);
CREATE TABLE IF NOT EXISTS audiences (
  id TEXT PRIMARY KEY, name TEXT NOT NULL, kind TEXT NOT NULL CHECK (kind IN ('board','investors','public','custom')),
  cadence TEXT NOT NULL DEFAULT 'monthly', due_rule TEXT NOT NULL DEFAULT '{}', lag_days INTEGER NOT NULL DEFAULT 0,
  approver TEXT, enabled INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
CREATE UNIQUE INDEX IF NOT EXISTS audiences_built_in ON audiences(kind) WHERE kind <> 'custom';
CREATE TABLE IF NOT EXISTS audience_sources (
  id TEXT PRIMARY KEY, audience_id TEXT NOT NULL REFERENCES audiences(id) ON DELETE CASCADE, author TEXT NOT NULL,
  created_at TEXT NOT NULL, UNIQUE(audience_id, author));
CREATE TABLE IF NOT EXISTS disclosure_revisions (
  id TEXT PRIMARY KEY, audience_id TEXT NOT NULL REFERENCES audiences(id) ON DELETE CASCADE, created_by TEXT NOT NULL,
  created_at TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS disclosure_revisions_audience ON disclosure_revisions(audience_id, created_at);
CREATE TABLE IF NOT EXISTS disclosure_rules (
  id TEXT PRIMARY KEY, audience_id TEXT NOT NULL REFERENCES audiences(id) ON DELETE CASCADE, data_class TEXT NOT NULL,
  ref_id TEXT NOT NULL DEFAULT '', mode TEXT NOT NULL CHECK (mode IN ('show','rounded','range','delayed','hidden')),
  lag_days INTEGER NOT NULL DEFAULT 0, rounding TEXT, updated_at TEXT NOT NULL,
  UNIQUE(audience_id, data_class, ref_id));
CREATE TABLE IF NOT EXISTS editions (
  id TEXT PRIMARY KEY, audience_id TEXT NOT NULL REFERENCES audiences(id), author TEXT NOT NULL,
  period_start TEXT NOT NULL, period_end TEXT NOT NULL, version INTEGER NOT NULL DEFAULT 1, supersedes_id TEXT,
  status TEXT NOT NULL CHECK (status IN ('drafting','held','ready','awaiting_approval','published','superseded','retracted')),
  title TEXT NOT NULL DEFAULT '', body_json TEXT NOT NULL DEFAULT '{}', payload_hash TEXT, approval_id TEXT,
  disclosure_revision_id TEXT, approved_by TEXT, published_at TEXT, retracted_at TEXT, retracted_by TEXT,
  retraction_note TEXT, correction_note TEXT, override_note TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS editions_stream ON editions(audience_id, status, published_at);
CREATE UNIQUE INDEX IF NOT EXISTS editions_one_per_period
  ON editions(audience_id, author, period_start, period_end, version);
CREATE TABLE IF NOT EXISTS edition_sources (
  id TEXT PRIMARY KEY, edition_id TEXT NOT NULL REFERENCES editions(id) ON DELETE CASCADE, update_id TEXT NOT NULL,
  body_sha256 TEXT NOT NULL, body TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS edition_sources_edition ON edition_sources(edition_id);
CREATE TABLE IF NOT EXISTS edition_checks (
  id TEXT PRIMARY KEY, edition_id TEXT NOT NULL REFERENCES editions(id) ON DELETE CASCADE, version INTEGER NOT NULL,
  kind TEXT NOT NULL, outcome TEXT NOT NULL, detail_json TEXT NOT NULL DEFAULT '{}', created_at TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS edition_checks_edition ON edition_checks(edition_id, created_at);
CREATE TABLE IF NOT EXISTS readers (
  id TEXT PRIMARY KEY, email TEXT NOT NULL, idp_issuer TEXT, idp_sub TEXT, name TEXT, added_by TEXT NOT NULL,
  notify INTEGER NOT NULL DEFAULT 1, created_at TEXT NOT NULL, removed_at TEXT);
CREATE UNIQUE INDEX IF NOT EXISTS readers_email ON readers(lower(email)) WHERE removed_at IS NULL;
CREATE UNIQUE INDEX IF NOT EXISTS readers_account ON readers(idp_issuer, idp_sub)
  WHERE idp_sub IS NOT NULL AND removed_at IS NULL;
CREATE TABLE IF NOT EXISTS audience_readers (
  id TEXT PRIMARY KEY, audience_id TEXT NOT NULL REFERENCES audiences(id) ON DELETE CASCADE,
  reader_id TEXT NOT NULL REFERENCES readers(id) ON DELETE CASCADE, created_at TEXT NOT NULL,
  UNIQUE(audience_id, reader_id));
CREATE INDEX IF NOT EXISTS audience_readers_reader ON audience_readers(reader_id);
CREATE TABLE IF NOT EXISTS reader_sessions (
  id TEXT PRIMARY KEY, reader_id TEXT NOT NULL REFERENCES readers(id) ON DELETE CASCADE, token_hash TEXT NOT NULL UNIQUE,
  created_at TEXT NOT NULL, expires_at TEXT NOT NULL, revoked_at TEXT);
CREATE INDEX IF NOT EXISTS reader_sessions_reader ON reader_sessions(reader_id);
CREATE TABLE IF NOT EXISTS reader_invites (
  id TEXT PRIMARY KEY, audience_id TEXT NOT NULL REFERENCES audiences(id) ON DELETE CASCADE,
  token_hash TEXT NOT NULL UNIQUE, created_by TEXT NOT NULL, created_at TEXT NOT NULL, expires_at TEXT NOT NULL,
  used_at TEXT, used_by TEXT, revoked_at TEXT);
CREATE TABLE IF NOT EXISTS edition_views (
  id TEXT PRIMARY KEY, edition_id TEXT NOT NULL REFERENCES editions(id) ON DELETE CASCADE,
  reader_id TEXT NOT NULL REFERENCES readers(id) ON DELETE CASCADE, viewed_at TEXT NOT NULL,
  UNIQUE(edition_id, reader_id));
CREATE TABLE IF NOT EXISTS reader_messages (
  id TEXT PRIMARY KEY, reader_id TEXT NOT NULL REFERENCES readers(id) ON DELETE CASCADE,
  edition_id TEXT NOT NULL REFERENCES editions(id) ON DELETE CASCADE,
  kind TEXT NOT NULL CHECK (kind IN ('reply','can_help')), ask_id TEXT, body TEXT NOT NULL, task_id TEXT,
  created_at TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS reader_messages_quota ON reader_messages(reader_id, created_at);
CREATE TABLE IF NOT EXISTS reader_notices (
  id TEXT PRIMARY KEY, edition_id TEXT NOT NULL REFERENCES editions(id) ON DELETE CASCADE,
  reader_id TEXT NOT NULL REFERENCES readers(id) ON DELETE CASCADE, status TEXT NOT NULL DEFAULT 'pending'
    CHECK (status IN ('pending','sent','failed','unavailable','cancelled')),
  attempts INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
  UNIQUE(edition_id, reader_id));
CREATE INDEX IF NOT EXISTS reader_notices_pending ON reader_notices(status, created_at);
CREATE TABLE IF NOT EXISTS page_stats (
  id TEXT PRIMARY KEY, source_kind TEXT NOT NULL CHECK (source_kind IN ('kpi','finance_line','finance_metric')),
  ref_id TEXT NOT NULL, data_class TEXT NOT NULL, label TEXT, definition TEXT,
  chart TEXT NOT NULL DEFAULT 'number' CHECK (chart IN ('number','line','bars')), position INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL, UNIQUE(source_kind, ref_id));
CREATE TABLE IF NOT EXISTS stat_board_approvals (
  id TEXT PRIMARY KEY, audience_id TEXT NOT NULL REFERENCES audiences(id) ON DELETE CASCADE, config_hash TEXT NOT NULL,
  disclosure_revision_id TEXT, approval_id TEXT NOT NULL, approved_by TEXT, created_at TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS stat_board_approvals_audience ON stat_board_approvals(audience_id, created_at);
CREATE TABLE IF NOT EXISTS page_releases (
  id TEXT PRIMARY KEY, path TEXT NOT NULL, reason TEXT NOT NULL, created_at TEXT NOT NULL);
"""
