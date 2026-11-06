-- Finance's ad spend import (backend/finance/ad_import.py): the ad platforms' own daily spend per campaign, so an open
-- month's ad budgets read live spend before the card statement posts. Integer cents, ISO days, UTC times. No foreign
-- keys into the finance tables: those are made by the finance schema, which may run after this.

-- One per Google Ads or Meta Ads tool Finance imports from. `credentials` is {slot: credential id}: which stored key
-- holds each value the platform needs (never a value). `account_id` is the books line the spend books as. `status`:
-- never, ok, reconnect (the platform refused the login), failed (anything else; `last_error` says what).
CREATE TABLE IF NOT EXISTS fin_ad_imports (
 id TEXT PRIMARY KEY,
 tool_id TEXT NOT NULL UNIQUE,
 platform TEXT NOT NULL CHECK (platform IN ('google_ads','meta_ads')),
 enabled INTEGER NOT NULL DEFAULT 0,
 history_from TEXT NOT NULL,
 account_id TEXT NOT NULL DEFAULT '',
 credentials TEXT NOT NULL DEFAULT '{}',
 status TEXT NOT NULL DEFAULT 'never',
 last_import_at TEXT,
 last_error TEXT NOT NULL DEFAULT '',
 run_requested_at TEXT,
 created_by TEXT NOT NULL,
 created_at TEXT NOT NULL,
 updated_at TEXT NOT NULL
);

-- An ad account a tool's login can see, and the bot whose budgets its spend counts in (NULL: not assigned).
CREATE TABLE IF NOT EXISTS fin_ad_accounts (
 id TEXT PRIMARY KEY,
 tool_id TEXT NOT NULL,
 platform TEXT NOT NULL CHECK (platform IN ('google_ads','meta_ads')),
 external_id TEXT NOT NULL,
 name TEXT NOT NULL DEFAULT '',
 currency TEXT NOT NULL DEFAULT 'USD',
 bot_slug TEXT,
 active INTEGER NOT NULL DEFAULT 1,
 created_at TEXT NOT NULL,
 updated_at TEXT NOT NULL,
 UNIQUE (tool_id, external_id)
);

-- A day's spend on one campaign, as the platform last reported it: a re-import overwrites the day.
CREATE TABLE IF NOT EXISTS fin_ad_spends (
 id TEXT PRIMARY KEY,
 ad_account_id TEXT NOT NULL REFERENCES fin_ad_accounts(id) ON DELETE CASCADE,
 day TEXT NOT NULL,
 campaign_external_id TEXT NOT NULL,
 campaign_name TEXT NOT NULL DEFAULT '',
 amount_cents BIGINT NOT NULL,
 currency TEXT NOT NULL,
 fetched_at TEXT NOT NULL,
 UNIQUE (ad_account_id, day, campaign_external_id)
);
CREATE INDEX IF NOT EXISTS fin_ad_spends_by_day ON fin_ad_spends(day, ad_account_id);

-- A budget warning a bot was given from live ad spend: once per level (warn, limit, pace) per budget per period
-- (budgeting.event_key).
CREATE TABLE IF NOT EXISTS fin_ad_warnings (
 budget_id TEXT NOT NULL,
 period_key TEXT NOT NULL,
 level TEXT NOT NULL CHECK (level IN ('warn','limit','pace')),
 bot_slug TEXT NOT NULL,
 created_at TEXT NOT NULL,
 PRIMARY KEY (budget_id, period_key, level)
);
