-- Attribution rules by class, location and project (backend/finance/store/attribution.py): a rule's match kind widens
-- from vendor, P&L account and money account to those plus class, location and project. SQLite cannot change a CHECK
-- in place, so fin_attribution_rules is rebuilt keeping its rows. The rebuild copies out, drops, creates and copies
-- back rather than renaming: a rename re-checks every trigger, and on a new file presences_meeting_delete names
-- task_roles, which the module code creates after the migrations.
CREATE TABLE fin_attribution_rules_tmp (
 id TEXT PRIMARY KEY,
 book_id TEXT NOT NULL,
 name TEXT NOT NULL,
 match_kind TEXT NOT NULL,
 match_id TEXT NOT NULL,
 splits TEXT NOT NULL DEFAULT '[]',
 enabled INTEGER NOT NULL DEFAULT 1,
 apply_from TEXT,
 created_by TEXT NOT NULL,
 created_at TEXT NOT NULL,
 updated_at TEXT,
 deleted_at TEXT
);
INSERT INTO fin_attribution_rules_tmp(id, book_id, name, match_kind, match_id, splits, enabled, apply_from,
 created_by, created_at, updated_at, deleted_at)
 SELECT id, book_id, name, match_kind, match_id, splits, enabled, apply_from, created_by, created_at, updated_at,
 deleted_at
 FROM fin_attribution_rules;
DROP TABLE fin_attribution_rules;
CREATE TABLE fin_attribution_rules (
 id TEXT PRIMARY KEY,
 book_id TEXT NOT NULL,
 name TEXT NOT NULL,
 match_kind TEXT NOT NULL CHECK (match_kind IN ('vendor','account','money_account','class','location','project')),
 match_id TEXT NOT NULL,
 splits TEXT NOT NULL DEFAULT '[]',
 enabled INTEGER NOT NULL DEFAULT 1,
 apply_from TEXT,
 created_by TEXT NOT NULL,
 created_at TEXT NOT NULL,
 updated_at TEXT,
 deleted_at TEXT
);
INSERT INTO fin_attribution_rules(id, book_id, name, match_kind, match_id, splits, enabled, apply_from,
 created_by, created_at, updated_at, deleted_at)
 SELECT id, book_id, name, match_kind, match_id, splits, enabled, apply_from, created_by, created_at, updated_at,
 deleted_at
 FROM fin_attribution_rules_tmp;
DROP TABLE fin_attribution_rules_tmp;
CREATE INDEX IF NOT EXISTS fin_attribution_rules_book ON fin_attribution_rules(book_id, deleted_at);
CREATE UNIQUE INDEX IF NOT EXISTS fin_attribution_rules_match ON fin_attribution_rules(book_id, match_kind, match_id)
 WHERE deleted_at IS NULL;
