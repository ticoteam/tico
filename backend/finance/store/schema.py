"""Every Finance table (spec §4), created by the core migration chain (backend/store.py: cloud migration 64, and 65
for phase 1's additions, 66 for phase 2's), including the ones later phases fill, so the schema has one starting point.

Every row has `id` (core's id format), `created_at` and `created_by` (a teammate, `system:finance` or `rule:<id>`).
Money is BIGINT minor units beside its ISO currency. Booleans are 0/1 integers, times ISO text, JSON is text.
"""
import re
import sqlite3

TABLES = """
CREATE TABLE IF NOT EXISTS fin_settings (
  key TEXT PRIMARY KEY, value_json TEXT NOT NULL, created_at TEXT NOT NULL, created_by TEXT NOT NULL,
  updated_at TEXT NOT NULL, updated_by TEXT NOT NULL);

CREATE TABLE IF NOT EXISTS fin_books (
  id TEXT PRIMARY KEY, name TEXT NOT NULL, base_currency TEXT NOT NULL,
  basis TEXT NOT NULL DEFAULT 'cash' CHECK (basis IN ('cash','accrual')),
  fiscal_year_start INTEGER NOT NULL DEFAULT 1 CHECK (fiscal_year_start BETWEEN 1 AND 12),
  mode TEXT NOT NULL DEFAULT 'native' CHECK (mode IN ('native','mirror')), mirror_connection_id TEXT,
  created_at TEXT NOT NULL, created_by TEXT NOT NULL);

CREATE TABLE IF NOT EXISTS fin_accounts (
  id TEXT PRIMARY KEY, book_id TEXT NOT NULL REFERENCES fin_books(id), code TEXT NOT NULL, name TEXT NOT NULL,
  type TEXT NOT NULL CHECK (type IN ('asset','liability','equity','income','expense')),
  subtype TEXT NOT NULL DEFAULT '', parent_id TEXT REFERENCES fin_accounts(id), active INTEGER NOT NULL DEFAULT 1,
  external_ref TEXT, created_at TEXT NOT NULL, created_by TEXT NOT NULL, updated_at TEXT,
  UNIQUE (book_id, code));

CREATE TABLE IF NOT EXISTS fin_periods (
  id TEXT PRIMARY KEY, book_id TEXT NOT NULL REFERENCES fin_books(id), start_date TEXT NOT NULL,
  end_date TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'open' CHECK (status IN ('open','closed')),
  closed_at TEXT, closed_by TEXT, created_at TEXT NOT NULL, created_by TEXT NOT NULL,
  UNIQUE (book_id, start_date));

CREATE TABLE IF NOT EXISTS fin_entries (
  id TEXT PRIMARY KEY, book_id TEXT NOT NULL REFERENCES fin_books(id), entry_date TEXT NOT NULL,
  memo TEXT NOT NULL DEFAULT '',
  source TEXT NOT NULL CHECK (source IN ('manual','import','bank','card','bill','invoice','payment','usage','sync')),
  source_id TEXT NOT NULL DEFAULT '', status TEXT NOT NULL DEFAULT 'posted' CHECK (status IN ('posted','reversed')),
  reverses_id TEXT REFERENCES fin_entries(id), period_id TEXT REFERENCES fin_periods(id), action_id TEXT,
  created_at TEXT NOT NULL, created_by TEXT NOT NULL);

CREATE TABLE IF NOT EXISTS fin_lines (
  id TEXT PRIMARY KEY, entry_id TEXT NOT NULL REFERENCES fin_entries(id), book_id TEXT NOT NULL,
  line_no INTEGER NOT NULL, entry_date TEXT NOT NULL, account_id TEXT NOT NULL REFERENCES fin_accounts(id),
  amount BIGINT NOT NULL, currency TEXT NOT NULL, original_amount BIGINT NOT NULL, fx_rate TEXT NOT NULL DEFAULT '1',
  contact_id TEXT NOT NULL DEFAULT '', description TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL, created_by TEXT NOT NULL, role TEXT NOT NULL DEFAULT '');

CREATE TABLE IF NOT EXISTS fin_dimensions (
  id TEXT PRIMARY KEY, kind TEXT NOT NULL CHECK (kind IN ('project','custom')), value TEXT NOT NULL,
  name TEXT NOT NULL, active INTEGER NOT NULL DEFAULT 1, created_at TEXT NOT NULL, created_by TEXT NOT NULL,
  UNIQUE (kind, value));

CREATE TABLE IF NOT EXISTS fin_line_tags (
  line_id TEXT NOT NULL REFERENCES fin_lines(id),
  kind TEXT NOT NULL CHECK (kind IN ('team','teammate','budget','project','custom')), value TEXT NOT NULL,
  created_at TEXT NOT NULL, created_by TEXT NOT NULL, PRIMARY KEY (line_id, kind));

CREATE TABLE IF NOT EXISTS fin_rollups (
  book_id TEXT NOT NULL, day TEXT NOT NULL, account_id TEXT NOT NULL, tagset TEXT NOT NULL,
  team TEXT NOT NULL DEFAULT '', teammate TEXT NOT NULL DEFAULT '', vendor TEXT NOT NULL DEFAULT '',
  amount BIGINT NOT NULL DEFAULT 0, line_count BIGINT NOT NULL DEFAULT 0,
  PRIMARY KEY (book_id, day, account_id, tagset));

CREATE TABLE IF NOT EXISTS fin_contacts (
  id TEXT PRIMARY KEY, book_id TEXT NOT NULL REFERENCES fin_books(id), name TEXT NOT NULL,
  kind TEXT NOT NULL DEFAULT 'vendor' CHECK (kind IN ('vendor','customer','both')), emails_json TEXT NOT NULL DEFAULT '[]',
  default_account_id TEXT REFERENCES fin_accounts(id), is_1099 INTEGER NOT NULL DEFAULT 0, tax_id_ref TEXT,
  payment_ref TEXT, active INTEGER NOT NULL DEFAULT 1, created_at TEXT NOT NULL, created_by TEXT NOT NULL,
  updated_at TEXT);

CREATE TABLE IF NOT EXISTS fin_bills (
  id TEXT PRIMARY KEY, book_id TEXT NOT NULL REFERENCES fin_books(id), vendor_id TEXT REFERENCES fin_contacts(id),
  number TEXT NOT NULL DEFAULT '', bill_date TEXT NOT NULL, due_date TEXT,
  status TEXT NOT NULL DEFAULT 'draft' CHECK (status IN ('draft','open','scheduled','paid','void')),
  currency TEXT NOT NULL, total BIGINT NOT NULL DEFAULT 0, commitment_id TEXT, entry_id TEXT REFERENCES fin_entries(id),
  created_at TEXT NOT NULL, created_by TEXT NOT NULL, updated_at TEXT);

CREATE TABLE IF NOT EXISTS fin_bill_lines (
  id TEXT PRIMARY KEY, bill_id TEXT NOT NULL REFERENCES fin_bills(id), account_id TEXT REFERENCES fin_accounts(id),
  amount BIGINT NOT NULL, description TEXT NOT NULL DEFAULT '', tags_json TEXT NOT NULL DEFAULT '{}',
  created_at TEXT NOT NULL, created_by TEXT NOT NULL);

CREATE TABLE IF NOT EXISTS fin_invoices (
  id TEXT PRIMARY KEY, book_id TEXT NOT NULL REFERENCES fin_books(id), customer_id TEXT REFERENCES fin_contacts(id),
  number TEXT NOT NULL DEFAULT '', invoice_date TEXT NOT NULL, due_date TEXT,
  status TEXT NOT NULL DEFAULT 'draft' CHECK (status IN ('draft','sent','partial','paid','void')),
  currency TEXT NOT NULL, total BIGINT NOT NULL DEFAULT 0, amount_paid BIGINT NOT NULL DEFAULT 0, payment_link TEXT,
  entry_id TEXT REFERENCES fin_entries(id), created_at TEXT NOT NULL, created_by TEXT NOT NULL, updated_at TEXT);

CREATE TABLE IF NOT EXISTS fin_invoice_lines (
  id TEXT PRIMARY KEY, invoice_id TEXT NOT NULL REFERENCES fin_invoices(id), account_id TEXT REFERENCES fin_accounts(id),
  amount BIGINT NOT NULL, description TEXT NOT NULL DEFAULT '', tags_json TEXT NOT NULL DEFAULT '{}',
  created_at TEXT NOT NULL, created_by TEXT NOT NULL);

CREATE TABLE IF NOT EXISTS fin_attachments (
  id TEXT PRIMARY KEY, book_id TEXT NOT NULL REFERENCES fin_books(id), subject_kind TEXT NOT NULL,
  subject_id TEXT NOT NULL, file_id TEXT NOT NULL, created_at TEXT NOT NULL, created_by TEXT NOT NULL);

CREATE TABLE IF NOT EXISTS fin_money_accounts (
  id TEXT PRIMARY KEY, book_id TEXT NOT NULL REFERENCES fin_books(id),
  kind TEXT NOT NULL CHECK (kind IN ('bank','card_program','stripe','gateway_credit')), name TEXT NOT NULL,
  provider TEXT NOT NULL DEFAULT '', connection_id TEXT, account_id TEXT REFERENCES fin_accounts(id), last4 TEXT,
  holds_cash INTEGER NOT NULL DEFAULT 1, balance BIGINT, balance_currency TEXT, balance_as_of TEXT,
  created_at TEXT NOT NULL, created_by TEXT NOT NULL, updated_at TEXT, provider_account_id TEXT,
  subtype TEXT NOT NULL DEFAULT '', available_balance BIGINT, status TEXT NOT NULL DEFAULT 'active');

CREATE TABLE IF NOT EXISTS fin_feed_txns (
  id TEXT PRIMARY KEY, money_account_id TEXT NOT NULL REFERENCES fin_money_accounts(id), provider_id TEXT NOT NULL,
  txn_date TEXT NOT NULL, amount BIGINT NOT NULL, currency TEXT NOT NULL, counterparty TEXT NOT NULL DEFAULT '',
  description TEXT NOT NULL DEFAULT '',
  status TEXT NOT NULL DEFAULT 'new' CHECK (status IN ('new','matched','posted','ignored')),
  entry_id TEXT REFERENCES fin_entries(id), commitment_id TEXT, created_at TEXT NOT NULL, created_by TEXT NOT NULL,
  updated_at TEXT, pending INTEGER NOT NULL DEFAULT 0, provider_status TEXT NOT NULL DEFAULT '', posted_at TEXT,
  kind TEXT NOT NULL DEFAULT '', card_id TEXT, hold_id TEXT, reconciliation_id TEXT, version INTEGER NOT NULL DEFAULT 0,
  request_id TEXT, raw_json TEXT NOT NULL DEFAULT '{}', UNIQUE (money_account_id, provider_id));

CREATE TABLE IF NOT EXISTS fin_reconciliations (
  id TEXT PRIMARY KEY, money_account_id TEXT NOT NULL REFERENCES fin_money_accounts(id), statement_end TEXT NOT NULL,
  statement_balance BIGINT NOT NULL, cleared_json TEXT NOT NULL DEFAULT '[]', difference BIGINT NOT NULL DEFAULT 0,
  status TEXT NOT NULL DEFAULT 'open' CHECK (status IN ('open','done')), completed_at TEXT,
  created_at TEXT NOT NULL, created_by TEXT NOT NULL, cleared_total BIGINT NOT NULL DEFAULT 0, statement_id TEXT,
  action_id TEXT, undone_at TEXT, undone_by TEXT);

CREATE TABLE IF NOT EXISTS fin_budgets (
  id TEXT PRIMARY KEY, book_id TEXT NOT NULL REFERENCES fin_books(id), name TEXT NOT NULL, owner TEXT NOT NULL DEFAULT '',
  period TEXT NOT NULL DEFAULT 'month' CHECK (period IN ('month','quarter','year','once')),
  account_id TEXT REFERENCES fin_accounts(id), catch_all INTEGER NOT NULL DEFAULT 0, team TEXT NOT NULL DEFAULT '',
  teammate TEXT NOT NULL DEFAULT '', vendor TEXT NOT NULL DEFAULT '', parent_id TEXT REFERENCES fin_budgets(id),
  warn_pct INTEGER NOT NULL DEFAULT 80,
  at_limit TEXT NOT NULL DEFAULT 'warn' CHECK (at_limit IN ('warn','ask','decline','hold_payment')),
  rollover TEXT NOT NULL DEFAULT 'none' CHECK (rollover IN ('none','unused')), active INTEGER NOT NULL DEFAULT 1,
  created_at TEXT NOT NULL, created_by TEXT NOT NULL, updated_at TEXT);

CREATE TABLE IF NOT EXISTS fin_budget_versions (
  id TEXT PRIMARY KEY, budget_id TEXT NOT NULL REFERENCES fin_budgets(id), version INTEGER NOT NULL,
  effective_from TEXT NOT NULL, amount BIGINT NOT NULL CHECK (amount >= 0), currency TEXT NOT NULL,
  created_at TEXT NOT NULL, created_by TEXT NOT NULL, UNIQUE (budget_id, version));

CREATE TABLE IF NOT EXISTS fin_projections (
  id TEXT PRIMARY KEY, book_id TEXT NOT NULL REFERENCES fin_books(id), account_id TEXT NOT NULL REFERENCES fin_accounts(id),
  month TEXT NOT NULL, team TEXT NOT NULL DEFAULT '', teammate TEXT NOT NULL DEFAULT '', amount BIGINT NOT NULL,
  currency TEXT NOT NULL,
  method TEXT NOT NULL CHECK (method IN ('trend','run_rate','recurring','fixed','manual')), set_by TEXT NOT NULL,
  as_of TEXT NOT NULL, created_at TEXT NOT NULL, created_by TEXT NOT NULL,
  UNIQUE (book_id, account_id, month, team, teammate));

CREATE TABLE IF NOT EXISTS fin_holds (
  id TEXT PRIMARY KEY, budget_id TEXT NOT NULL REFERENCES fin_budgets(id), amount BIGINT NOT NULL, currency TEXT NOT NULL,
  source TEXT NOT NULL, source_id TEXT NOT NULL DEFAULT '', target TEXT NOT NULL DEFAULT '', commitment_id TEXT,
  held_on TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'held' CHECK (status IN ('held','settled','released')), settled_amount BIGINT,
  entry_id TEXT REFERENCES fin_entries(id), expires_at TEXT, settled_at TEXT, released_at TEXT,
  created_at TEXT NOT NULL, created_by TEXT NOT NULL);

CREATE TABLE IF NOT EXISTS fin_allocations (
  id TEXT PRIMARY KEY, budget_id TEXT NOT NULL REFERENCES fin_budgets(id),
  target_kind TEXT NOT NULL CHECK (target_kind IN ('card','gateway_key','teammate')), target_id TEXT NOT NULL,
  amount BIGINT NOT NULL CHECK (amount >= 0), currency TEXT NOT NULL, hold_id TEXT REFERENCES fin_holds(id),
  created_at TEXT NOT NULL, created_by TEXT NOT NULL, updated_at TEXT, UNIQUE (budget_id, target_kind, target_id));

CREATE TABLE IF NOT EXISTS fin_cards (
  id TEXT PRIMARY KEY, provider TEXT NOT NULL, provider_card_id TEXT, holder TEXT NOT NULL,
  budget_id TEXT REFERENCES fin_budgets(id), money_account_id TEXT REFERENCES fin_money_accounts(id),
  kind TEXT NOT NULL CHECK (kind IN ('standing','single_use','person')), limit_amount BIGINT, limit_interval TEXT,
  currency TEXT NOT NULL, merchant_locks_json TEXT NOT NULL DEFAULT '[]', category_locks_json TEXT NOT NULL DEFAULT '[]',
  expires_at TEXT, status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active','frozen','cancelled')), last4 TEXT,
  created_at TEXT NOT NULL, created_by TEXT NOT NULL, updated_at TEXT, nickname TEXT NOT NULL DEFAULT '',
  provider_limit BIGINT, sync_status TEXT NOT NULL DEFAULT '', sync_error TEXT, synced_at TEXT);

CREATE TABLE IF NOT EXISTS fin_card_auths (
  id TEXT PRIMARY KEY, card_id TEXT NOT NULL REFERENCES fin_cards(id), provider_auth_id TEXT, amount BIGINT NOT NULL,
  currency TEXT NOT NULL, merchant TEXT NOT NULL DEFAULT '', mode TEXT NOT NULL CHECK (mode IN ('live','synced')),
  decision TEXT NOT NULL, reasons_json TEXT NOT NULL DEFAULT '[]', hold_id TEXT REFERENCES fin_holds(id),
  commitment_id TEXT, created_at TEXT NOT NULL, created_by TEXT NOT NULL);

CREATE TABLE IF NOT EXISTS fin_actions (
  id TEXT PRIMARY KEY, kind TEXT NOT NULL, payload_json TEXT NOT NULL, actor TEXT NOT NULL,
  on_behalf_of TEXT NOT NULL DEFAULT '', rule_id TEXT NOT NULL DEFAULT '', idempotency_key TEXT NOT NULL UNIQUE,
  rule_results_json TEXT NOT NULL DEFAULT '[]',
  status TEXT NOT NULL CHECK (status IN ('committed','needs_approval','denied','failed','expired','withdrawn')),
  approval_id TEXT, approval_hash TEXT, approval_by TEXT, approval_to TEXT, result_json TEXT NOT NULL DEFAULT '{}', code TEXT NOT NULL DEFAULT '',
  reason TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL, created_by TEXT NOT NULL, updated_at TEXT NOT NULL);

CREATE TABLE IF NOT EXISTS fin_rules (
  id TEXT PRIMARY KEY, kind TEXT NOT NULL CHECK (kind IN ('gate','posting')), name TEXT NOT NULL,
  words TEXT NOT NULL DEFAULT '', when_json TEXT NOT NULL DEFAULT '{}', outcome_json TEXT NOT NULL DEFAULT '{}',
  examples_json TEXT NOT NULL DEFAULT '[]', external_rule_id TEXT, owner TEXT NOT NULL DEFAULT '',
  active INTEGER NOT NULL DEFAULT 1, created_at TEXT NOT NULL, created_by TEXT NOT NULL, updated_at TEXT);

CREATE TABLE IF NOT EXISTS fin_rule_runs (
  id TEXT PRIMARY KEY, rule_id TEXT NOT NULL, action_id TEXT, subject TEXT NOT NULL DEFAULT '',
  inputs_json TEXT NOT NULL DEFAULT '{}', outcome_json TEXT NOT NULL DEFAULT '{}',
  created_at TEXT NOT NULL, created_by TEXT NOT NULL);

CREATE TABLE IF NOT EXISTS fin_connections (
  id TEXT PRIMARY KEY, provider TEXT NOT NULL,
  purpose TEXT NOT NULL CHECK (purpose IN ('bank','cards','books','payments')), credential_ref TEXT,
  status TEXT NOT NULL DEFAULT 'new', cursor TEXT, last_ok_at TEXT, last_error TEXT,
  created_at TEXT NOT NULL, created_by TEXT NOT NULL, updated_at TEXT, name TEXT NOT NULL DEFAULT '',
  settings_json TEXT NOT NULL DEFAULT '{}', events_cursor TEXT, last_sync_at TEXT, next_sync_at TEXT);

CREATE TABLE IF NOT EXISTS fin_external_refs (
  id TEXT PRIMARY KEY, connection_id TEXT NOT NULL REFERENCES fin_connections(id), local_table TEXT NOT NULL,
  local_id TEXT NOT NULL, external_id TEXT NOT NULL, created_at TEXT NOT NULL, created_by TEXT NOT NULL,
  UNIQUE (connection_id, local_table, external_id));

CREATE TABLE IF NOT EXISTS fin_outbox (
  id TEXT PRIMARY KEY, connection_id TEXT REFERENCES fin_connections(id), action_id TEXT, op TEXT NOT NULL,
  payload_json TEXT NOT NULL DEFAULT '{}', idempotency_key TEXT NOT NULL UNIQUE, attempts INTEGER NOT NULL DEFAULT 0,
  next_try_at TEXT, status TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending','sent','failed','withheld')),
  last_error TEXT, created_at TEXT NOT NULL, created_by TEXT NOT NULL, updated_at TEXT);

CREATE TABLE IF NOT EXISTS fin_inbox (
  id TEXT PRIMARY KEY, connection_id TEXT REFERENCES fin_connections(id), provider TEXT NOT NULL,
  provider_event_id TEXT NOT NULL, payload_json TEXT NOT NULL DEFAULT '{}', status TEXT NOT NULL DEFAULT 'new',
  processed_at TEXT, created_at TEXT NOT NULL, created_by TEXT NOT NULL, UNIQUE (provider, provider_event_id));

CREATE TABLE IF NOT EXISTS fin_events (
  id TEXT PRIMARY KEY, name TEXT NOT NULL, subject TEXT NOT NULL, period_key TEXT NOT NULL DEFAULT '',
  payload_json TEXT NOT NULL DEFAULT '{}', drained_at TEXT, created_at TEXT NOT NULL, created_by TEXT NOT NULL,
  UNIQUE (name, subject, period_key));

CREATE TABLE IF NOT EXISTS fin_usage_snapshots (
  id TEXT PRIMARY KEY, month TEXT NOT NULL, as_of TEXT NOT NULL, ai_nano BIGINT NOT NULL DEFAULT 0,
  video_nano BIGINT NOT NULL DEFAULT 0, rows_json TEXT NOT NULL DEFAULT '[]', posted_at TEXT, ignored_at TEXT,
  created_at TEXT NOT NULL, created_by TEXT NOT NULL, UNIQUE (month, as_of));

CREATE TABLE IF NOT EXISTS fin_rights (
  id TEXT PRIMARY KEY, teammate TEXT NOT NULL, right_name TEXT NOT NULL, granted_by TEXT NOT NULL, expires_at TEXT,
  revoked_at TEXT, revoked_by TEXT, created_at TEXT NOT NULL, created_by TEXT NOT NULL);

CREATE TABLE IF NOT EXISTS fin_view_defaults (
  role TEXT PRIMARY KEY, policy TEXT NOT NULL, created_at TEXT NOT NULL, created_by TEXT NOT NULL, updated_at TEXT);

CREATE TABLE IF NOT EXISTS fin_view_versions (
  id INTEGER PRIMARY KEY CHECK (id = 1), version BIGINT NOT NULL DEFAULT 0, reason TEXT NOT NULL DEFAULT '',
  updated_at TEXT NOT NULL);

CREATE TABLE IF NOT EXISTS fin_settlements (
  id TEXT PRIMARY KEY, book_id TEXT NOT NULL REFERENCES fin_books(id),
  from_kind TEXT NOT NULL CHECK (from_kind IN ('payment','feed','credit','account','expected')),
  from_id TEXT NOT NULL DEFAULT '',
  to_kind TEXT NOT NULL CHECK (to_kind IN ('bill','invoice')), to_id TEXT NOT NULL, amount BIGINT NOT NULL,
  settled_on TEXT NOT NULL, entry_id TEXT, action_id TEXT, voided_at TEXT, voided_by TEXT,
  created_at TEXT NOT NULL, created_by TEXT NOT NULL);

CREATE TABLE IF NOT EXISTS fin_cash_rollups (
  book_id TEXT NOT NULL, day TEXT NOT NULL, account_id TEXT NOT NULL, tagset TEXT NOT NULL,
  team TEXT NOT NULL DEFAULT '', teammate TEXT NOT NULL DEFAULT '', vendor TEXT NOT NULL DEFAULT '',
  amount BIGINT NOT NULL DEFAULT 0, PRIMARY KEY (book_id, day, account_id, tagset));

CREATE TABLE IF NOT EXISTS fin_view_grants (
  id TEXT PRIMARY KEY, subject_kind TEXT NOT NULL CHECK (subject_kind IN ('teammate','team')), subject TEXT NOT NULL,
  new_members INTEGER NOT NULL DEFAULT 0, members_json TEXT NOT NULL DEFAULT '[]',
  what TEXT NOT NULL CHECK (what IN ('everything','pnl','expenses','pnl_lines','balance_sheet','bs_lines','payables',
                                     'receivables','sources')),
  account_ids_json TEXT NOT NULL DEFAULT '[]', whose TEXT NOT NULL CHECK (whose IN ('own','team','company')),
  whose_team TEXT NOT NULL DEFAULT '', granted_by TEXT NOT NULL, expires_at TEXT,
  status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active','suspended','expired','revoked')),
  suspended_reason TEXT, revoked_at TEXT, revoked_by TEXT, created_at TEXT NOT NULL, created_by TEXT NOT NULL);

CREATE TABLE IF NOT EXISTS fin_recipients (
  id TEXT PRIMARY KEY, connection_id TEXT REFERENCES fin_connections(id), provider_recipient_id TEXT,
  name TEXT NOT NULL, method TEXT NOT NULL CHECK (method IN ('ach','wire','check','rtp')), last4 TEXT,
  details_hash TEXT NOT NULL, details_ref TEXT, version INTEGER NOT NULL DEFAULT 1,
  replaces_id TEXT REFERENCES fin_recipients(id),
  status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active','replaced')), contact_id TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL, created_by TEXT NOT NULL, updated_at TEXT);

-- The provider's own users (Mercury issues cards only to them), as last read with a connection's token: a card holder
-- picker. Replaced on every sync.
CREATE TABLE IF NOT EXISTS fin_provider_users (
  connection_id TEXT NOT NULL REFERENCES fin_connections(id), provider_user_id TEXT NOT NULL,
  name TEXT NOT NULL DEFAULT '', email TEXT NOT NULL DEFAULT '', role TEXT NOT NULL DEFAULT '',
  updated_at TEXT NOT NULL, PRIMARY KEY (connection_id, provider_user_id));

CREATE TABLE IF NOT EXISTS fin_payments (
  id TEXT PRIMARY KEY, book_id TEXT NOT NULL REFERENCES fin_books(id), action_id TEXT NOT NULL,
  idempotency_key TEXT NOT NULL UNIQUE, recipient_id TEXT NOT NULL REFERENCES fin_recipients(id),
  recipient_version INTEGER NOT NULL, money_account_id TEXT NOT NULL REFERENCES fin_money_accounts(id),
  amount BIGINT NOT NULL CHECK (amount > 0), currency TEXT NOT NULL,
  method TEXT NOT NULL CHECK (method IN ('ach','wire','check','rtp')), memo TEXT NOT NULL DEFAULT '',
  obligation TEXT NOT NULL DEFAULT '', account_id TEXT NOT NULL REFERENCES fin_accounts(id),
  tags_json TEXT NOT NULL DEFAULT '{}',
  mode TEXT NOT NULL DEFAULT 'approval_queue' CHECK (mode IN ('approval_queue','direct')),
  status TEXT NOT NULL DEFAULT 'queued' CHECK (status IN ('queued','sending','awaiting_bank_approval','submitted',
                                                          'settled','returned','failed','cancelled','unknown')),
  purpose_json TEXT NOT NULL DEFAULT '{}', counted_json TEXT NOT NULL DEFAULT '[]', unknown_since TEXT, flagged_at TEXT,
  provider_request_id TEXT, provider_txn_id TEXT, hold_id TEXT REFERENCES fin_holds(id), attempts INTEGER NOT NULL DEFAULT 0,
  next_try_at TEXT, last_error TEXT, settled_at TEXT, created_at TEXT NOT NULL, created_by TEXT NOT NULL,
  updated_at TEXT);

CREATE TABLE IF NOT EXISTS fin_rule_counters (
  rule_id TEXT NOT NULL, period_key TEXT NOT NULL, subject TEXT NOT NULL DEFAULT '', amount BIGINT NOT NULL DEFAULT 0,
  count INTEGER NOT NULL DEFAULT 0, updated_at TEXT NOT NULL, PRIMARY KEY (rule_id, period_key, subject));

CREATE TABLE IF NOT EXISTS fin_statements (
  id TEXT PRIMARY KEY, money_account_id TEXT NOT NULL REFERENCES fin_money_accounts(id), provider_id TEXT,
  start_date TEXT, end_date TEXT NOT NULL, starting_balance BIGINT, ending_balance BIGINT NOT NULL,
  currency TEXT NOT NULL, source TEXT NOT NULL CHECK (source IN ('api','manual')), created_at TEXT NOT NULL,
  created_by TEXT NOT NULL, UNIQUE (money_account_id, end_date));

CREATE TABLE IF NOT EXISTS fin_private_lines (
  account_id TEXT PRIMARY KEY REFERENCES fin_accounts(id),
  others_see TEXT NOT NULL DEFAULT 'totals' CHECK (others_see IN ('totals','hidden')),
  full_detail_json TEXT NOT NULL DEFAULT '[]', created_at TEXT NOT NULL, created_by TEXT NOT NULL, updated_at TEXT);
"""

INDEXES = """
CREATE INDEX IF NOT EXISTS fin_accounts_book ON fin_accounts(book_id, type);
CREATE INDEX IF NOT EXISTS fin_entries_date ON fin_entries(book_id, entry_date);
CREATE INDEX IF NOT EXISTS fin_entries_source ON fin_entries(source, source_id);
CREATE INDEX IF NOT EXISTS fin_entries_reverses ON fin_entries(reverses_id);
CREATE INDEX IF NOT EXISTS fin_lines_entry ON fin_lines(entry_id);
CREATE INDEX IF NOT EXISTS fin_lines_account ON fin_lines(account_id, entry_date);
CREATE INDEX IF NOT EXISTS fin_lines_contact ON fin_lines(contact_id, entry_date);
CREATE INDEX IF NOT EXISTS fin_line_tags_value ON fin_line_tags(kind, value);
CREATE INDEX IF NOT EXISTS fin_rollups_account ON fin_rollups(account_id, day);
CREATE INDEX IF NOT EXISTS fin_rollups_team ON fin_rollups(team, day);
CREATE INDEX IF NOT EXISTS fin_rollups_teammate ON fin_rollups(teammate, day);
CREATE INDEX IF NOT EXISTS fin_rollups_vendor ON fin_rollups(vendor, day);
CREATE INDEX IF NOT EXISTS fin_budgets_parent ON fin_budgets(book_id, parent_id);
CREATE INDEX IF NOT EXISTS fin_budgets_account ON fin_budgets(account_id);
CREATE INDEX IF NOT EXISTS fin_holds_budget ON fin_holds(budget_id, status, held_on);
CREATE INDEX IF NOT EXISTS fin_holds_expiry ON fin_holds(status, expires_at);
CREATE INDEX IF NOT EXISTS fin_holds_source ON fin_holds(source, source_id);
CREATE INDEX IF NOT EXISTS fin_holds_target ON fin_holds(target, status);
CREATE INDEX IF NOT EXISTS fin_actions_approval ON fin_actions(approval_id);
CREATE INDEX IF NOT EXISTS fin_actions_status ON fin_actions(status, created_at);
CREATE INDEX IF NOT EXISTS fin_events_drain ON fin_events(drained_at, created_at);
CREATE INDEX IF NOT EXISTS fin_usage_snapshots_open ON fin_usage_snapshots(posted_at, month);
CREATE INDEX IF NOT EXISTS fin_rights_teammate ON fin_rights(teammate, right_name);
CREATE INDEX IF NOT EXISTS fin_view_grants_subject ON fin_view_grants(subject_kind, subject, status);
CREATE INDEX IF NOT EXISTS fin_feed_txns_status ON fin_feed_txns(status, txn_date);
CREATE INDEX IF NOT EXISTS fin_outbox_due ON fin_outbox(status, next_try_at);
CREATE INDEX IF NOT EXISTS fin_cards_holder ON fin_cards(holder, status);
CREATE INDEX IF NOT EXISTS fin_cards_budget ON fin_cards(budget_id);
CREATE INDEX IF NOT EXISTS fin_feed_txns_account ON fin_feed_txns(money_account_id, txn_date);
CREATE INDEX IF NOT EXISTS fin_feed_txns_card ON fin_feed_txns(card_id, txn_date);
CREATE INDEX IF NOT EXISTS fin_feed_txns_request ON fin_feed_txns(request_id);
CREATE INDEX IF NOT EXISTS fin_feed_txns_entry ON fin_feed_txns(entry_id);
CREATE INDEX IF NOT EXISTS fin_money_accounts_provider ON fin_money_accounts(connection_id, provider_account_id);
CREATE INDEX IF NOT EXISTS fin_cards_provider ON fin_cards(provider, provider_card_id);
CREATE INDEX IF NOT EXISTS fin_recipients_provider ON fin_recipients(connection_id, provider_recipient_id);
CREATE INDEX IF NOT EXISTS fin_payments_status ON fin_payments(status, next_try_at);
CREATE UNIQUE INDEX IF NOT EXISTS fin_payments_live ON fin_payments(obligation) WHERE obligation <> '' AND
  status IN ('queued','sending','awaiting_bank_approval','submitted','unknown');
CREATE INDEX IF NOT EXISTS fin_inbox_status ON fin_inbox(status, created_at);
CREATE INDEX IF NOT EXISTS fin_rule_runs_rule ON fin_rule_runs(rule_id, created_at);
CREATE INDEX IF NOT EXISTS fin_rule_runs_action ON fin_rule_runs(action_id, created_at);
CREATE INDEX IF NOT EXISTS fin_reconciliations_account ON fin_reconciliations(money_account_id, statement_end);
CREATE INDEX IF NOT EXISTS fin_settlements_to ON fin_settlements(to_kind, to_id);
CREATE INDEX IF NOT EXISTS fin_settlements_from ON fin_settlements(from_kind, from_id);
CREATE INDEX IF NOT EXISTS fin_attachments_subject ON fin_attachments(subject_kind, subject_id);
CREATE INDEX IF NOT EXISTS fin_bills_status ON fin_bills(status, due_date);
CREATE INDEX IF NOT EXISTS fin_bills_pay_on ON fin_bills(status, pay_on);
CREATE INDEX IF NOT EXISTS fin_bills_list ON fin_bills(book_id, bill_date, id);
CREATE INDEX IF NOT EXISTS fin_bill_lines_bill ON fin_bill_lines(bill_id);
CREATE INDEX IF NOT EXISTS fin_invoices_status ON fin_invoices(status, due_date);
CREATE INDEX IF NOT EXISTS fin_invoices_list ON fin_invoices(book_id, invoice_date, id);
CREATE INDEX IF NOT EXISTS fin_invoice_lines_invoice ON fin_invoice_lines(invoice_id);
CREATE INDEX IF NOT EXISTS fin_contacts_name ON fin_contacts(book_id, name);
CREATE INDEX IF NOT EXISTS fin_rules_kind ON fin_rules(kind, active, priority);
"""

# Invariants 2 and 3 again in the database, so no statement (Finance's or anyone's) can break them. SQLite syntax: a
# Postgres store writes the same rules as plpgsql trigger functions.
TRIGGERS = """
CREATE TRIGGER IF NOT EXISTS fin_lines_no_update BEFORE UPDATE ON fin_lines
BEGIN SELECT RAISE(ABORT, 'fin_lines are immutable: post a reversing entry'); END;

CREATE TRIGGER IF NOT EXISTS fin_lines_no_delete BEFORE DELETE ON fin_lines
BEGIN SELECT RAISE(ABORT, 'fin_lines are immutable: post a reversing entry'); END;

CREATE TRIGGER IF NOT EXISTS fin_entries_no_delete BEFORE DELETE ON fin_entries
BEGIN SELECT RAISE(ABORT, 'fin_entries are immutable: post a reversing entry'); END;

CREATE TRIGGER IF NOT EXISTS fin_entries_only_reverse BEFORE UPDATE ON fin_entries
WHEN NOT (OLD.status = 'posted' AND NEW.status = 'reversed' AND NEW.id = OLD.id AND NEW.book_id = OLD.book_id
          AND NEW.entry_date = OLD.entry_date AND NEW.memo = OLD.memo AND NEW.source = OLD.source
          AND NEW.source_id = OLD.source_id AND NEW.reverses_id IS OLD.reverses_id AND NEW.period_id IS OLD.period_id
          AND NEW.action_id IS OLD.action_id AND NEW.created_at = OLD.created_at AND NEW.created_by = OLD.created_by)
BEGIN SELECT RAISE(ABORT, 'fin_entries are immutable: only posted -> reversed may change'); END;

CREATE TRIGGER IF NOT EXISTS fin_entries_open_period BEFORE INSERT ON fin_entries
WHEN EXISTS (SELECT 1 FROM fin_periods p WHERE p.book_id = NEW.book_id AND p.status = 'closed'
             AND NEW.entry_date BETWEEN p.start_date AND p.end_date)
BEGIN SELECT RAISE(ABORT, 'period closed'); END;

CREATE TRIGGER IF NOT EXISTS fin_lines_open_period BEFORE INSERT ON fin_lines
WHEN EXISTS (SELECT 1 FROM fin_periods p WHERE p.book_id = NEW.book_id AND p.status = 'closed'
             AND NEW.entry_date BETWEEN p.start_date AND p.end_date)
BEGIN SELECT RAISE(ABORT, 'period closed'); END;
"""


# Columns phase 1 added to tables an install may already have from migration 64 (table, column, definition). Migration
# 65 (`phase1`) adds whichever is missing, so an upgraded install ends with the same schema as a new one.
ADDED = (
    ("fin_lines", "role", "TEXT NOT NULL DEFAULT ''"),
    ("fin_money_accounts", "provider_account_id", "TEXT"), ("fin_money_accounts", "subtype", "TEXT NOT NULL DEFAULT ''"),
    ("fin_money_accounts", "available_balance", "BIGINT"),
    ("fin_money_accounts", "status", "TEXT NOT NULL DEFAULT 'active'"),
    ("fin_feed_txns", "pending", "INTEGER NOT NULL DEFAULT 0"),
    ("fin_feed_txns", "provider_status", "TEXT NOT NULL DEFAULT ''"), ("fin_feed_txns", "posted_at", "TEXT"),
    ("fin_feed_txns", "kind", "TEXT NOT NULL DEFAULT ''"), ("fin_feed_txns", "card_id", "TEXT"),
    ("fin_feed_txns", "hold_id", "TEXT"), ("fin_feed_txns", "reconciliation_id", "TEXT"),
    ("fin_feed_txns", "version", "INTEGER NOT NULL DEFAULT 0"), ("fin_feed_txns", "request_id", "TEXT"),
    ("fin_feed_txns", "raw_json", "TEXT NOT NULL DEFAULT '{}'"),
    ("fin_reconciliations", "cleared_total", "BIGINT NOT NULL DEFAULT 0"),
    ("fin_reconciliations", "statement_id", "TEXT"), ("fin_reconciliations", "action_id", "TEXT"),
    ("fin_reconciliations", "undone_at", "TEXT"), ("fin_reconciliations", "undone_by", "TEXT"),
    ("fin_cards", "nickname", "TEXT NOT NULL DEFAULT ''"), ("fin_cards", "provider_limit", "BIGINT"),
    ("fin_cards", "sync_status", "TEXT NOT NULL DEFAULT ''"), ("fin_cards", "sync_error", "TEXT"),
    ("fin_cards", "synced_at", "TEXT"),
    ("fin_connections", "name", "TEXT NOT NULL DEFAULT ''"),
    ("fin_connections", "settings_json", "TEXT NOT NULL DEFAULT '{}'"), ("fin_connections", "events_cursor", "TEXT"),
    ("fin_connections", "last_sync_at", "TEXT"), ("fin_connections", "next_sync_at", "TEXT"),
    ("fin_outbox", "claimed_at", "TEXT"), ("fin_cards", "provider_status", "TEXT"), ("fin_cards", "allocation_due", "INTEGER"),
    ("fin_inbox", "attempts", "INTEGER NOT NULL DEFAULT 0"), ("fin_inbox", "next_try_at", "TEXT"),
    ("fin_inbox", "tried_json", "TEXT NOT NULL DEFAULT '[]'"),
    ("fin_payments", "counted_json", "TEXT NOT NULL DEFAULT '[]'"),
    # phase 2: bills, invoices, contacts, posting rules and the cash view (migration 66)
    ("fin_entries", "basis", "TEXT NOT NULL DEFAULT 'both' CHECK (basis IN ('both','accrual'))"),
    ("fin_bills", "terms_days", "INTEGER"), ("fin_bills", "memo", "TEXT NOT NULL DEFAULT ''"),
    ("fin_bills", "origin", "TEXT NOT NULL DEFAULT 'manual'"), ("fin_bills", "approved_by", "TEXT"),
    ("fin_bills", "approved_at", "TEXT"), ("fin_bills", "schedule_json", "TEXT NOT NULL DEFAULT '{}'"),
    ("fin_bills", "amount_paid", "BIGINT NOT NULL DEFAULT 0"), ("fin_bills", "voided_at", "TEXT"),
    ("fin_bills", "pay_on", "TEXT"), ("fin_bill_lines", "line_no", "INTEGER NOT NULL DEFAULT 0"),
    ("fin_invoice_lines", "line_no", "INTEGER NOT NULL DEFAULT 0"),
    ("fin_invoices", "terms_days", "INTEGER"), ("fin_invoices", "memo", "TEXT NOT NULL DEFAULT ''"),
    ("fin_invoices", "sent_at", "TEXT"), ("fin_invoices", "written_off", "BIGINT NOT NULL DEFAULT 0"),
    ("fin_invoices", "voided_at", "TEXT"),
    ("fin_contacts", "tax_id_last4", "TEXT"), ("fin_rules", "priority", "INTEGER NOT NULL DEFAULT 100"),
)


def table_names() -> list[str]:
    """Every fin_* table the migration makes."""
    return re.findall(r"CREATE TABLE IF NOT EXISTS (fin_\w+)", TABLES)


def statements(script: str) -> list[str]:
    """The complete statements of a script; a trigger body keeps its inner semicolons."""
    out: list[str] = []
    buffer = ""
    for line in script.splitlines(keepends=True):
        buffer += line
        if sqlite3.complete_statement(buffer):
            out.append(buffer.strip())
            buffer = ""
    if buffer.strip():
        out.append(buffer.strip())
    return out


def _add_columns(c: sqlite3.Connection) -> None:
    have: dict[str, set[str]] = {}
    for table, column, ddl in ADDED:
        if table not in have:
            have[table] = {str(r[1]) for r in c.execute(f"PRAGMA table_info({table})").fetchall()}
        if column not in have[table]:
            c.execute(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}")
            have[table].add(column)


def create(c: sqlite3.Connection) -> None:
    """Every table, index and trigger, statement by statement inside the caller's transaction (executescript would
    commit it). Idempotent: on an install made before a later phase it adds the new tables and columns too."""
    for statement in statements(TABLES):
        c.execute(statement)
    _add_columns(c)
    for script in (INDEXES, TRIGGERS):
        for statement in statements(script):
            c.execute(statement)
    from . import tico
    tico.watch_org(c)


def phase1(c: sqlite3.Connection) -> None:
    """Cloud migration 65: phase 1's tables, columns, indexes and triggers on an install migration 64 made before
    them. The same statements as `create`, which skips whatever exists."""
    create(c)


def phase2(c: sqlite3.Connection) -> None:
    """Cloud migration 66: phase 2's settlements, cash view and document columns on an install made before them. The
    same statements as `create` again."""
    create(c)
