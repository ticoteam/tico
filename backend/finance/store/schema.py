"""Every Finance table (spec §4), created by the core migration chain (backend/store.py: cloud migration 64, and 65
for phase 1's additions, 66 for phase 2's, 67 for phase 3's), including the ones later phases fill, so the schema has one starting point.

Every row has `id` (core's id format), `created_at` and `created_by` (a teammate, `system:finance` or `rule:<id>`).
Money is BIGINT minor units beside its ISO currency. Booleans are 0/1 integers, times ISO text, JSON is text.
"""
import json
import re
import sqlite3
from typing import cast

from ..core.ledger import name_key
from ..core.org import SYSTEM

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
  id TEXT PRIMARY KEY, kind TEXT NOT NULL CHECK (kind IN ('project','custom','class')), value TEXT NOT NULL,
  name TEXT NOT NULL, active INTEGER NOT NULL DEFAULT 1, created_at TEXT NOT NULL, created_by TEXT NOT NULL,
  UNIQUE (kind, value));

CREATE TABLE IF NOT EXISTS fin_line_tags (
  line_id TEXT NOT NULL REFERENCES fin_lines(id),
  kind TEXT NOT NULL CHECK (kind IN ('team','teammate','budget','project','custom','location','class')), value TEXT NOT
  NULL,
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

-- Every name a vendor contact has gone by (spec §3: one key per vendor), as L.name_key: a bank or card row under one
-- posts under the contact's id, and lines posted under one before count for it. Never deleted; a name maps to one
-- contact.
CREATE TABLE IF NOT EXISTS fin_vendor_aliases (
  book_id TEXT NOT NULL REFERENCES fin_books(id), name_key TEXT NOT NULL,
  contact_id TEXT NOT NULL REFERENCES fin_contacts(id), created_at TEXT NOT NULL,
  PRIMARY KEY (book_id, name_key));

-- How a vendor name key with no contact reads: the text it was last posted under (a bank or card row's counterparty,
-- a payee's name, an entry's vendor), the newest entry date winning. Display only; nothing counts by it.
CREATE TABLE IF NOT EXISTS fin_vendor_labels (
  book_id TEXT NOT NULL REFERENCES fin_books(id), name_key TEXT NOT NULL, label TEXT NOT NULL,
  seen_on TEXT NOT NULL, PRIMARY KEY (book_id, name_key));

-- Vendor keys lines carried before names were keyed folded (phase 1 posted a name as written), found by migration
-- 66: lines never change, so a name key (and a contact, through its aliases) also counts lines under these.
CREATE TABLE IF NOT EXISTS fin_vendor_legacy (
  book_id TEXT NOT NULL REFERENCES fin_books(id), raw TEXT NOT NULL, name_key TEXT NOT NULL,
  PRIMARY KEY (book_id, raw));

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

CREATE TABLE IF NOT EXISTS fin_card_saves (
  id TEXT PRIMARY KEY, book_id TEXT NOT NULL REFERENCES fin_books(id), card_id TEXT NOT NULL REFERENCES fin_cards(id),
  vendor_key TEXT NOT NULL, vendor_label TEXT NOT NULL DEFAULT '', active INTEGER NOT NULL DEFAULT 1 CHECK (active IN (0,1)),
  saved_by TEXT NOT NULL, saved_at TEXT NOT NULL, removed_by TEXT, removed_at TEXT, created_at TEXT NOT NULL,
  created_by TEXT NOT NULL, updated_at TEXT NOT NULL, UNIQUE (card_id, vendor_key));

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

-- Which provider record each mirrored local row is (spec §4.1). external_id is the provider's kind and id ("Vendor:9"),
-- revision its version (QuickBooks SyncToken), status active, deleted or unmapped (taken in later, when its references are).
CREATE TABLE IF NOT EXISTS fin_external_refs (
  id TEXT PRIMARY KEY, connection_id TEXT NOT NULL REFERENCES fin_connections(id), local_table TEXT NOT NULL,
  local_id TEXT NOT NULL, external_id TEXT NOT NULL, created_at TEXT NOT NULL, created_by TEXT NOT NULL,
  kind TEXT NOT NULL DEFAULT '', revision TEXT NOT NULL DEFAULT '', status TEXT NOT NULL DEFAULT 'active',
  updated_at TEXT, UNIQUE (connection_id, local_table, external_id));

-- A closed month a provider change restated: the change posted in the open period (reversal and replacement, or a
-- new entry), and this row names the closed period it changed. The period itself is never reopened.
CREATE TABLE IF NOT EXISTS fin_restatements (
  id TEXT PRIMARY KEY, book_id TEXT NOT NULL REFERENCES fin_books(id), period_id TEXT NOT NULL REFERENCES fin_periods(id),
  day TEXT NOT NULL, change TEXT NOT NULL CHECK (change IN ('added','replaced','deleted')),
  entry_id TEXT REFERENCES fin_entries(id), reversal_id TEXT REFERENCES fin_entries(id),
  connection_id TEXT REFERENCES fin_connections(id), external_id TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL, created_by TEXT NOT NULL);

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

CREATE TABLE IF NOT EXISTS fin_credit_refunds (
  id TEXT PRIMARY KEY, book_id TEXT NOT NULL REFERENCES fin_books(id),
  credit_entry_id TEXT NOT NULL, refund_entry_id TEXT NOT NULL, amount BIGINT NOT NULL, day TEXT NOT NULL,
  voided_at TEXT, created_at TEXT NOT NULL, created_by TEXT NOT NULL);

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

CREATE TABLE IF NOT EXISTS fin_raw_imports (
  id TEXT PRIMARY KEY, book_id TEXT NOT NULL REFERENCES fin_books(id), content_hash TEXT NOT NULL,
  basis TEXT NOT NULL CHECK (basis IN ('cash','accrual')), source_name TEXT NOT NULL DEFAULT '',
  periods INTEGER NOT NULL CHECK (periods > 0), row_count INTEGER NOT NULL, gross BIGINT NOT NULL,
  action_id TEXT NOT NULL, created_at TEXT NOT NULL, created_by TEXT NOT NULL);

CREATE TABLE IF NOT EXISTS fin_close_snapshots (
  id TEXT PRIMARY KEY, book_id TEXT NOT NULL REFERENCES fin_books(id),
  period_id TEXT NOT NULL UNIQUE REFERENCES fin_periods(id), month TEXT NOT NULL,
  accounts_json TEXT NOT NULL, pnl_json TEXT NOT NULL, balances_json TEXT NOT NULL,
  restated_json TEXT NOT NULL DEFAULT '[]', created_at TEXT NOT NULL, created_by TEXT NOT NULL,
  UNIQUE (book_id, month));

CREATE TABLE IF NOT EXISTS fin_transfers (
  id TEXT PRIMARY KEY, book_id TEXT NOT NULL REFERENCES fin_books(id), action_id TEXT NOT NULL,
  from_money_account_id TEXT NOT NULL REFERENCES fin_money_accounts(id),
  to_money_account_id TEXT NOT NULL REFERENCES fin_money_accounts(id),
  amount BIGINT NOT NULL CHECK (amount > 0), currency TEXT NOT NULL, memo TEXT NOT NULL DEFAULT '',
  idempotency_key TEXT NOT NULL UNIQUE,
  status TEXT NOT NULL DEFAULT 'queued' CHECK (status IN ('queued','submitted','unknown','failed','posted','cancelled')),
  provider_txn_id TEXT, provider_credit_txn_id TEXT, last_error TEXT, from_feed_id TEXT, to_feed_id TEXT,
  settled_at TEXT, submitted_at TEXT, find_after TEXT,
  counted_json TEXT NOT NULL DEFAULT '[]',
  created_at TEXT NOT NULL, created_by TEXT NOT NULL, updated_at TEXT,
  CHECK (from_money_account_id <> to_money_account_id));
"""

INDEXES = """
CREATE INDEX IF NOT EXISTS fin_accounts_book ON fin_accounts(book_id, type);
CREATE INDEX IF NOT EXISTS fin_entries_date ON fin_entries(book_id, entry_date);
CREATE INDEX IF NOT EXISTS fin_entries_source ON fin_entries(source, source_id);
CREATE INDEX IF NOT EXISTS fin_entries_reverses ON fin_entries(reverses_id);
CREATE INDEX IF NOT EXISTS fin_raw_imports_hash ON fin_raw_imports(book_id, content_hash);
CREATE INDEX IF NOT EXISTS fin_lines_entry ON fin_lines(entry_id);
CREATE INDEX IF NOT EXISTS fin_lines_account ON fin_lines(account_id, entry_date);
CREATE INDEX IF NOT EXISTS fin_bill_lines_po_line ON fin_bill_lines(po_line_id);
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
CREATE INDEX IF NOT EXISTS fin_feed_txns_entry_line ON fin_feed_txns(entry_line_id);
CREATE INDEX IF NOT EXISTS fin_money_accounts_provider ON fin_money_accounts(connection_id, provider_account_id);
CREATE INDEX IF NOT EXISTS fin_cards_provider ON fin_cards(provider, provider_card_id);
CREATE INDEX IF NOT EXISTS fin_card_saves_vendor ON fin_card_saves(book_id, vendor_key, active);
CREATE INDEX IF NOT EXISTS fin_recipients_provider ON fin_recipients(connection_id, provider_recipient_id);
CREATE INDEX IF NOT EXISTS fin_payments_status ON fin_payments(status, next_try_at);
CREATE INDEX IF NOT EXISTS fin_payments_obligation ON fin_payments(obligation, created_at);
CREATE UNIQUE INDEX IF NOT EXISTS fin_payments_live ON fin_payments(obligation) WHERE obligation <> '' AND
  status IN ('queued','sending','awaiting_bank_approval','submitted','unknown');
CREATE INDEX IF NOT EXISTS fin_inbox_status ON fin_inbox(status, created_at);
CREATE INDEX IF NOT EXISTS fin_rule_runs_rule ON fin_rule_runs(rule_id, created_at);
CREATE INDEX IF NOT EXISTS fin_rule_runs_action ON fin_rule_runs(action_id, created_at);
CREATE INDEX IF NOT EXISTS fin_reconciliations_account ON fin_reconciliations(money_account_id, statement_end);
CREATE INDEX IF NOT EXISTS fin_settlements_to ON fin_settlements(to_kind, to_id);
CREATE INDEX IF NOT EXISTS fin_settlements_from ON fin_settlements(from_kind, from_id);
CREATE INDEX IF NOT EXISTS fin_credit_refunds_credit ON fin_credit_refunds(credit_entry_id);
CREATE INDEX IF NOT EXISTS fin_credit_refunds_refund ON fin_credit_refunds(refund_entry_id);
CREATE INDEX IF NOT EXISTS fin_settlements_entry ON fin_settlements(entry_id);
CREATE INDEX IF NOT EXISTS fin_attachments_subject ON fin_attachments(subject_kind, subject_id);
CREATE INDEX IF NOT EXISTS fin_bills_status ON fin_bills(status, due_date);
CREATE INDEX IF NOT EXISTS fin_bills_pay_on ON fin_bills(status, pay_on);
CREATE INDEX IF NOT EXISTS fin_bills_list ON fin_bills(book_id, bill_date, id);
CREATE INDEX IF NOT EXISTS fin_bill_lines_bill ON fin_bill_lines(bill_id);
CREATE INDEX IF NOT EXISTS fin_invoices_status ON fin_invoices(status, due_date);
CREATE INDEX IF NOT EXISTS fin_invoices_list ON fin_invoices(book_id, invoice_date, id);
CREATE INDEX IF NOT EXISTS fin_invoice_lines_invoice ON fin_invoice_lines(invoice_id);
CREATE INDEX IF NOT EXISTS fin_contacts_name ON fin_contacts(book_id, name);
CREATE INDEX IF NOT EXISTS fin_vendor_aliases_contact ON fin_vendor_aliases(contact_id);
CREATE INDEX IF NOT EXISTS fin_vendor_legacy_key ON fin_vendor_legacy(book_id, name_key);
CREATE INDEX IF NOT EXISTS fin_rules_kind ON fin_rules(kind, active, priority);
CREATE INDEX IF NOT EXISTS fin_external_refs_local ON fin_external_refs(connection_id, local_table, local_id);
CREATE INDEX IF NOT EXISTS fin_restatements_period ON fin_restatements(period_id);
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
    ("fin_outbox", "unknown", "INTEGER NOT NULL DEFAULT 0"),
    ("fin_bill_lines", "external_line", "TEXT NOT NULL DEFAULT ''"),
    ("fin_invoice_lines", "external_line", "TEXT NOT NULL DEFAULT ''"),
    ("fin_reconciliations", "cleared_total", "BIGINT NOT NULL DEFAULT 0"),
    ("fin_reconciliations", "statement_id", "TEXT"), ("fin_reconciliations", "action_id", "TEXT"),
    ("fin_reconciliations", "undone_at", "TEXT"), ("fin_reconciliations", "undone_by", "TEXT"),
    ("fin_cards", "nickname", "TEXT NOT NULL DEFAULT ''"), ("fin_cards", "provider_limit", "BIGINT"),
    ("fin_cards", "sync_status", "TEXT NOT NULL DEFAULT ''"), ("fin_cards", "sync_error", "TEXT"),
    ("fin_cards", "synced_at", "TEXT"),
    ("fin_cards", "freeze_by", "TEXT"), ("fin_cards", "freeze_budget_id", "TEXT"),
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
    ("fin_invoice_lines", "taxable", "INTEGER NOT NULL DEFAULT 1"),
    ("fin_invoices", "terms_days", "INTEGER"), ("fin_invoices", "memo", "TEXT NOT NULL DEFAULT ''"),
    ("fin_invoices", "sent_at", "TEXT"), ("fin_invoices", "written_off", "BIGINT NOT NULL DEFAULT 0"),
    ("fin_invoices", "voided_at", "TEXT"),
    ("fin_contacts", "tax_id_last4", "TEXT"), ("fin_rules", "priority", "INTEGER NOT NULL DEFAULT 100"),
    # phase 4: the mirror's external refs (kind, revision, status) on installs made before them (migration 68)
    ("fin_external_refs", "kind", "TEXT NOT NULL DEFAULT ''"), ("fin_external_refs", "revision", "TEXT NOT NULL DEFAULT ''"),
    ("fin_external_refs", "status", "TEXT NOT NULL DEFAULT 'active'"), ("fin_external_refs", "updated_at", "TEXT"),
    # the provider's id for a journal or credit memo line, so its tags follow the line across a revision
    ("fin_lines", "external_line", "TEXT NOT NULL DEFAULT ''"),
    # vendors: seats or licenses, and the next renewal date (spec §12; the renewal stage events come from watch)
    ("fin_contacts", "seats", "INTEGER"), ("fin_contacts", "renewal_on", "TEXT"),
    # the books gaps (migration 73): fin_transfers' columns added since it was first written
    ("fin_transfers", "from_feed_id", "TEXT"), ("fin_transfers", "to_feed_id", "TEXT"),
    ("fin_transfers", "settled_at", "TEXT"), ("fin_transfers", "submitted_at", "TEXT"),
    ("fin_transfers", "counted_json", "TEXT NOT NULL DEFAULT '[]'"),
    ("fin_transfers", "provider_credit_txn_id", "TEXT"), ("fin_transfers", "find_after", "TEXT"),
    # the journal line a bank row matched (migration 75), so a transfer's two legs on one account match one at a time
    ("fin_feed_txns", "entry_line_id", "TEXT"),
    # the shared item a bill or invoice line names (migration 76, backend/records)
    ("fin_bill_lines", "item_id", "TEXT"), ("fin_invoice_lines", "item_id", "TEXT"),
    ("fin_bill_lines", "po_line_id", "TEXT"),
    # a Stripe price's details (amount, currency, interval) on its own ref (backend/finance/stripe_sync.py catalog)
    ("fin_external_refs", "detail", "TEXT NOT NULL DEFAULT '{}'"),
)


# Tax (migration 70, spec §11): the sales tax jurisdictions (each one a liability account and its authority contact) and
# the external-rule seeds made for the outside obligations (the 1099-NEC, each jurisdiction's quarterly return).
TAX = """
CREATE TABLE IF NOT EXISTS fin_tax_jurisdictions (
  id TEXT PRIMARY KEY, book_id TEXT NOT NULL REFERENCES fin_books(id), code TEXT NOT NULL, name TEXT NOT NULL,
  account_id TEXT NOT NULL REFERENCES fin_accounts(id), authority_id TEXT NOT NULL DEFAULT '',
  due_day INTEGER NOT NULL CHECK (due_day BETWEEN 1 AND 28), created_at TEXT NOT NULL, created_by TEXT NOT NULL,
  UNIQUE (book_id, code), UNIQUE (account_id));

CREATE TABLE IF NOT EXISTS fin_tax_seeds (
  key TEXT PRIMARY KEY, rule_id TEXT NOT NULL, created_at TEXT NOT NULL);
"""

# Purchase orders and estimates (backend/finance/store/purchasing.py). An order's line holds budget while it is open;
# an estimate is invoiced once, so its invoice id is unique. A bill keeps billed on an order line that its provider
# revision no longer names in fin_bill_retained.
PURCHASING = """
CREATE TABLE IF NOT EXISTS fin_purchase_orders (
  id TEXT PRIMARY KEY, book_id TEXT NOT NULL REFERENCES fin_books(id), vendor_id TEXT NOT NULL REFERENCES fin_contacts(id),
  number TEXT NOT NULL DEFAULT '', po_date TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'draft' CHECK (status IN ('draft','sent','approved','closed','void')),
  closed_by TEXT NOT NULL DEFAULT '' CHECK (closed_by IN ('','bills','hand')),
  currency TEXT NOT NULL, total BIGINT NOT NULL DEFAULT 0, memo TEXT NOT NULL DEFAULT '',
  sent_at TEXT, approved_by TEXT, approved_at TEXT, closed_at TEXT, voided_at TEXT,
  created_at TEXT NOT NULL, created_by TEXT NOT NULL, updated_at TEXT);

CREATE TABLE IF NOT EXISTS fin_po_lines (
  id TEXT PRIMARY KEY, po_id TEXT NOT NULL REFERENCES fin_purchase_orders(id), line_no INTEGER NOT NULL,
  account_id TEXT REFERENCES fin_accounts(id), item_id TEXT, amount BIGINT NOT NULL CHECK (amount > 0),
  description TEXT NOT NULL DEFAULT '', tags_json TEXT NOT NULL DEFAULT '{}',
  created_at TEXT NOT NULL, created_by TEXT NOT NULL, UNIQUE (po_id, line_no));

CREATE TABLE IF NOT EXISTS fin_estimates (
  id TEXT PRIMARY KEY, book_id TEXT NOT NULL REFERENCES fin_books(id), customer_id TEXT NOT NULL REFERENCES fin_contacts(id),
  number TEXT NOT NULL DEFAULT '', estimate_date TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'draft' CHECK (status IN ('draft','sent','accepted','declined')),
  currency TEXT NOT NULL, total BIGINT NOT NULL DEFAULT 0, memo TEXT NOT NULL DEFAULT '',
  invoice_id TEXT REFERENCES fin_invoices(id), sent_at TEXT, decided_at TEXT,
  created_at TEXT NOT NULL, created_by TEXT NOT NULL, updated_at TEXT);

CREATE TABLE IF NOT EXISTS fin_estimate_lines (
  id TEXT PRIMARY KEY, estimate_id TEXT NOT NULL REFERENCES fin_estimates(id), line_no INTEGER NOT NULL,
  account_id TEXT REFERENCES fin_accounts(id), item_id TEXT, amount BIGINT NOT NULL CHECK (amount > 0),
  description TEXT NOT NULL DEFAULT '', tags_json TEXT NOT NULL DEFAULT '{}', taxable INTEGER,
  created_at TEXT NOT NULL, created_by TEXT NOT NULL, UNIQUE (estimate_id, line_no));

CREATE UNIQUE INDEX IF NOT EXISTS fin_estimates_invoice ON fin_estimates(invoice_id) WHERE invoice_id IS NOT NULL;

CREATE TABLE IF NOT EXISTS fin_bill_retained (
  bill_id TEXT NOT NULL REFERENCES fin_bills(id), po_line_id TEXT NOT NULL REFERENCES fin_po_lines(id),
  amount BIGINT NOT NULL CHECK (amount > 0), PRIMARY KEY (bill_id, po_line_id));
"""

def table_names() -> list[str]:
    """Every fin_* table the migration makes."""
    return re.findall(r"CREATE TABLE IF NOT EXISTS (fin_\w+)", TABLES + TAX + PURCHASING)


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
    for script in (TABLES, TAX, PURCHASING):
        for statement in statements(script):
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


def phase3(c: sqlite3.Connection) -> None:
    """Cloud migration 67: phase 3's saved-card record (fin_card_saves: which vendor accounts a card is saved on), and
    why an earlier cutoff froze its cards (freeze_provenance). The same statements as `create`, which skips whatever
    exists."""
    create(c)
    freeze_provenance(c)


def freeze_provenance(c: sqlite3.Connection) -> None:
    """A card a budget's cutoff froze before `freeze_by` existed has no record of why it is frozen, so it would never
    lift once its budget has room. The action log says: a system card_freeze for a budget is the cutoff's freeze, and it
    is the card's latest freeze or unfreeze when the card's last word is that one. Such a card that still has an
    allocation is marked `budget` with that budget. Every other frozen card keeps NULL (a person's freeze). Only NULL
    rows are read, so a second run changes nothing."""
    if not c.execute("SELECT 1 FROM fin_cards WHERE status='frozen' AND freeze_by IS NULL LIMIT 1").fetchone():
        return
    last: dict[str, str] = {}     # card id -> the budget its latest freeze or unfreeze was a cutoff of ("" if none)
    for kind, actor, payload, result in c.execute(
            "SELECT kind, actor, payload_json, result_json FROM fin_actions WHERE status='committed' AND "
            "kind IN ('card_freeze','card_unfreeze') ORDER BY created_at, rowid").fetchall():
        body = cast(dict[str, object], json.loads(str(payload or "{}")))
        cut = str(body.get("budget_id") or "") if kind == "card_freeze" and actor == SYSTEM else ""
        outcome = cast(dict[str, object], json.loads(str(result or "{}")))
        for card in cast(list[object], outcome.get("cards") or []):
            last[str(card)] = cut
    for card_id, budget_id in last.items():
        if budget_id:
            c.execute("UPDATE fin_cards SET freeze_by='budget', freeze_budget_id=? WHERE id=? AND status='frozen' AND "
                      "freeze_by IS NULL AND EXISTS (SELECT 1 FROM fin_allocations a "
                      "WHERE a.budget_id=fin_cards.budget_id AND a.target_kind='card' AND a.target_id=fin_cards.id "
                      "AND a.amount>0)", (budget_id, card_id))


def phase2(c: sqlite3.Connection) -> None:
    """Cloud migration 66: phase 2's settlements, cash view and document columns on an install made before them. The
    same statements as `create` again, then the vendor names phase 1 posted as written (vendor_keys_folded)."""
    create(c)
    vendor_keys_folded(c)


def feed_lines(c: sqlite3.Connection) -> None:
    """Cloud migration 75: the journal line each matched bank row took (fin_feed_txns.entry_line_id). A row matched
    before it takes the entry's line on its bank account for its amount, if the entry has one. Idempotent."""
    create(c)
    c.execute("UPDATE fin_feed_txns SET entry_line_id=(SELECT l.id FROM fin_lines l JOIN fin_money_accounts m ON "
              "m.id=fin_feed_txns.money_account_id WHERE l.entry_id=fin_feed_txns.entry_id AND "
              "l.account_id=m.account_id AND l.amount=fin_feed_txns.amount ORDER BY l.id LIMIT 1) "
              "WHERE entry_id IS NOT NULL AND entry_line_id IS NULL")


def phase4(c: sqlite3.Connection) -> None:
    """Cloud migration 68: the mirror's tables and columns (external refs with their kind, revision and status, the
    restatements a closed month takes, and each entry line's provider id), on an install made before them. The same
    statements as `create`, which skips whatever exists."""
    create(c)


STRIPE_INDEXES = """
CREATE INDEX IF NOT EXISTS fin_feed_txns_stripe_link ON fin_feed_txns(money_account_id, json_extract(raw_json, '$.link'));
CREATE INDEX IF NOT EXISTS fin_feed_txns_stripe_source ON fin_feed_txns(money_account_id, json_extract(raw_json, '$.source'));
"""


def stripe(c: sqlite3.Connection) -> None:
    """Cloud migration 69: the indexes the Stripe lookups search by, the charge link and the source id each Stripe row
    carries in its raw json (a refund's charge total is one of them). Idempotent. They live here, not in `create`,
    so an install from before 69 has no index on raw_json."""
    for statement in statements(STRIPE_INDEXES):
        c.execute(statement)


def raw_import(c: sqlite3.Connection) -> None:
    """Cloud migration 71: the history imports' table (spec §4.1). The same statements as `create`."""
    create(c)


def close_snapshots(c: sqlite3.Connection) -> None:
    """Cloud migration 72: the close snapshot table on an install that closed months before it. Months closed before
    it have no snapshot and read live. The same statements as `create` again, so it is idempotent."""
    create(c)


def gaps_books(c: sqlite3.Connection) -> None:
    """Cloud migration 73 (the books gaps): fin_transfers, for an install made before it. The same statements as
    `create`, which skips whatever exists, so a fresh install gets the table from `create` and this changes nothing."""
    create(c)


def vendor_keys_folded(c: sqlite3.Connection) -> None:
    """Phase 2 keys a plain vendor name folded (core/ledger.name_key); phase 1 posted it as written. Each vendor value
    lines carry that is no contact's id is recorded under its key (fin_vendor_legacy, when they differ) and labels the
    key (its newest entry date's text); a budget's vendor name is keyed. Idempotent."""
    rows = c.execute("SELECT l.book_id, l.contact_id, MAX(l.entry_date) FROM fin_lines l WHERE l.contact_id<>'' AND "
                     "NOT EXISTS (SELECT 1 FROM fin_contacts k WHERE k.id=l.contact_id) "
                     "GROUP BY l.book_id, l.contact_id").fetchall()
    for book_id, raw, day in rows:
        key = name_key(str(raw))
        if key != raw:
            c.execute("INSERT INTO fin_vendor_legacy(book_id, raw, name_key) VALUES(?,?,?) "
                      "ON CONFLICT(book_id, raw) DO NOTHING", (book_id, raw, key))
        c.execute("INSERT INTO fin_vendor_labels(book_id, name_key, label, seen_on) VALUES(?,?,?,?) "
                  "ON CONFLICT(book_id, name_key) DO UPDATE SET label=excluded.label, seen_on=excluded.seen_on "
                  "WHERE excluded.seen_on>fin_vendor_labels.seen_on", (book_id, key, str(raw).strip(), day))
    for bid, vendor in c.execute("SELECT b.id, b.vendor FROM fin_budgets b WHERE b.vendor<>'' AND NOT EXISTS "
                                 "(SELECT 1 FROM fin_contacts k WHERE k.id=b.vendor)").fetchall():
        if name_key(str(vendor)) != vendor:
            c.execute("UPDATE fin_budgets SET vendor=? WHERE id=?", (name_key(str(vendor)), bid))


def tax(c: sqlite3.Connection) -> None:
    """Cloud migration 70: the sales tax jurisdictions and the due dates seeded for the outside obligations. Idempotent."""
    for statement in statements(TAX):
        c.execute(statement)
    _add_columns(c)


def purchasing(c: sqlite3.Connection) -> None:
    """Cloud migration 77: purchase orders and estimates, and the bill line an order line is billed on. Idempotent:
    `create` makes whatever is missing."""
    create(c)


def catalog(c: sqlite3.Connection, at: str) -> None:
    """Cloud migration 76: the shared records (backend/records) and the location, class and item tags Finance's lines
    take.
    SQLite cannot widen a CHECK in place, so fin_dimensions and fin_line_tags are rebuilt keeping their rows, and each
    project dimension moves to projects under its value. A project tag holds the project's id, which is the legacy
    value, so no line's tags change and a closed month keeps its lines as they were. Idempotent."""
    from ...records import store as RS
    RS.create(c)
    _widen(c, "fin_dimensions", "'class'")
    _widen(c, "fin_line_tags", "'location'")
    create(c)
    _move_projects(c, at)


def _widen(c: sqlite3.Connection, table: str, marker: str) -> None:
    """Rebuild `table` from its TABLES definition when the table on disk predates `marker`. Rows are copied as they are."""
    row = c.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone()
    if row is None or marker in str(row[0]):
        return
    c.execute(f"ALTER TABLE {table} RENAME TO {table}_before")
    definition = next(s for s in statements(TABLES) if s.startswith(f"CREATE TABLE IF NOT EXISTS {table} "))
    c.execute(definition)
    cols = ",".join(str(r[1]) for r in c.execute(f"PRAGMA table_info({table}_before)").fetchall())
    c.execute(f"INSERT INTO {table}({cols}) SELECT {cols} FROM {table}_before")
    c.execute(f"DROP TABLE {table}_before")


def _move_projects(c: sqlite3.Connection, at: str) -> None:
    """A project dimension becomes a projects row under its value. A project tag value no record has yet becomes
    one under its own text, so the tag still points at a record."""
    c.execute("INSERT INTO projects(id, name, status, archived_at, created_at, created_by_id) "
              "SELECT value, name, 'open', CASE WHEN active THEN NULL ELSE ? END, ?, ? FROM fin_dimensions "
              "WHERE kind='project' AND value NOT IN (SELECT id FROM projects)", (at, at, SYSTEM))
    c.execute("DELETE FROM fin_dimensions WHERE kind='project'")
    c.execute("INSERT INTO projects(id, name, status, created_at, created_by_id) "
              "SELECT DISTINCT value, value, 'open', ?, ? FROM fin_line_tags WHERE kind='project' "
              "AND value NOT IN (SELECT id FROM projects)", (at, SYSTEM))
