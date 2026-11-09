-- Platform share (backend/finance/platform_share.py): an ad platform's charge counts for each bot in proportion to the
-- platform's own spend for that bot. Its attribution rows carry source 'platform'. SQLite cannot change a CHECK in
-- place, so fin_attributions is rebuilt with the fourth source; its rows carry over unchanged. The rebuild copies out,
-- drops, creates and copies back rather than renaming: a rename re-checks every trigger, and on a new file
-- presences_meeting_delete names task_roles, which the module code creates after the migrations.
CREATE TABLE fin_attributions_tmp (
 id TEXT PRIMARY KEY,
 book_id TEXT NOT NULL,
 line_id TEXT NOT NULL,
 splits TEXT NOT NULL DEFAULT '[]',
 source TEXT NOT NULL,
 rule_id TEXT,
 set_by TEXT NOT NULL,
 created_at TEXT NOT NULL,
 superseded_at TEXT,
 superseded_by TEXT
);
INSERT INTO fin_attributions_tmp(id, book_id, line_id, splits, source, rule_id, set_by, created_at,
 superseded_at, superseded_by)
 SELECT id, book_id, line_id, splits, source, rule_id, set_by, created_at, superseded_at, superseded_by
 FROM fin_attributions;
DROP TABLE fin_attributions;
CREATE TABLE fin_attributions (
 id TEXT PRIMARY KEY,
 book_id TEXT NOT NULL,
 line_id TEXT NOT NULL,
 splits TEXT NOT NULL DEFAULT '[]',
 source TEXT NOT NULL CHECK (source IN ('rule','person','bot','platform')),
 rule_id TEXT,
 set_by TEXT NOT NULL,
 created_at TEXT NOT NULL,
 superseded_at TEXT,
 superseded_by TEXT
);
INSERT INTO fin_attributions(id, book_id, line_id, splits, source, rule_id, set_by, created_at,
 superseded_at, superseded_by)
 SELECT id, book_id, line_id, splits, source, rule_id, set_by, created_at, superseded_at, superseded_by
 FROM fin_attributions_tmp;
DROP TABLE fin_attributions_tmp;
CREATE INDEX IF NOT EXISTS fin_attributions_line ON fin_attributions(line_id, created_at);
CREATE INDEX IF NOT EXISTS fin_attributions_rule ON fin_attributions(rule_id);
CREATE UNIQUE INDEX IF NOT EXISTS fin_attributions_in_force ON fin_attributions(line_id) WHERE superseded_at IS NULL;
