-- Finance attribution (backend/finance/attribution.py): which teammates and teams a posted P&L line counts for, in
-- shares. Tico-side only; nothing here reaches the books a provider keeps. Integer basis points, ISO days, UTC times.
-- No foreign keys into the finance tables: those are made by the finance schema, which may run after this.

-- One row per change to a line's attribution, immutable but for the superseded pair: a change stamps the row it
-- replaces (superseded_at, superseded_by) and inserts its own. The row in force is the one not superseded (one per
-- line, enforced by the partial unique index). `splits` is [] for a clear: the line counts as its tags again.
-- source: rule (a rule put it there), person, or bot (someone with finance.attribution set it by hand).
CREATE TABLE IF NOT EXISTS fin_attributions (
 id TEXT PRIMARY KEY,
 book_id TEXT NOT NULL,
 line_id TEXT NOT NULL,
 splits TEXT NOT NULL DEFAULT '[]',
 source TEXT NOT NULL CHECK (source IN ('rule','person','bot')),
 rule_id TEXT,
 set_by TEXT NOT NULL,
 created_at TEXT NOT NULL,
 superseded_at TEXT,
 superseded_by TEXT
);
CREATE INDEX IF NOT EXISTS fin_attributions_line ON fin_attributions(line_id, created_at);
CREATE INDEX IF NOT EXISTS fin_attributions_rule ON fin_attributions(rule_id);
CREATE UNIQUE INDEX IF NOT EXISTS fin_attributions_in_force ON fin_attributions(line_id) WHERE superseded_at IS NULL;

-- A rule: lines whose vendor, P&L account or money account (the bank or card side of the entry) matches take these
-- shares as they post, and with apply_from the lines already posted from that day that have no override. One live
-- rule per match. deleted_at keeps the rule's history.
CREATE TABLE IF NOT EXISTS fin_attribution_rules (
 id TEXT PRIMARY KEY,
 book_id TEXT NOT NULL,
 name TEXT NOT NULL,
 match_kind TEXT NOT NULL CHECK (match_kind IN ('vendor','account','money_account')),
 match_id TEXT NOT NULL,
 splits TEXT NOT NULL DEFAULT '[]',
 enabled INTEGER NOT NULL DEFAULT 1,
 apply_from TEXT,
 created_by TEXT NOT NULL,
 created_at TEXT NOT NULL,
 updated_at TEXT,
 deleted_at TEXT
);
CREATE INDEX IF NOT EXISTS fin_attribution_rules_book ON fin_attribution_rules(book_id, deleted_at);
CREATE UNIQUE INDEX IF NOT EXISTS fin_attribution_rules_match ON fin_attribution_rules(book_id, match_kind, match_id) WHERE deleted_at IS NULL;
