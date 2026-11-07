-- Finance's ad campaigns (backend/finance/campaigns.py): each campaign an ad account reports, under the account, with the
-- owner its spend counts for (a team or a teammate; NULL: the account's bot). Clicks and conversions come with the daily
-- spend the import reads. Integer cents, ISO days, UTC times.

-- One per campaign an ad account has spend for. `team_id` or `teammate` is the owner (both NULL: the account's bot).
-- `prior_*` is the owner before the latest change, which campaign_owner_undo puts back. The import upserts it: the name
-- follows the latest import, and the owner stays.
CREATE TABLE IF NOT EXISTS fin_ad_campaigns (
 id TEXT PRIMARY KEY,
 ad_account_id TEXT NOT NULL REFERENCES fin_ad_accounts(id) ON DELETE CASCADE,
 external_id TEXT NOT NULL,
 name TEXT NOT NULL DEFAULT '',
 team_id TEXT,
 teammate TEXT,
 owner_set_at TEXT,
 owner_set_by TEXT,
 prior_team_id TEXT,
 prior_teammate TEXT,
 first_seen_on TEXT NOT NULL,
 last_seen_on TEXT NOT NULL,
 created_at TEXT NOT NULL,
 updated_at TEXT NOT NULL,
 UNIQUE (ad_account_id, external_id)
);

-- Clicks and conversions per day per campaign, as the platform reports them (a restated day overwrites, as spend does).
-- Conversions are the platform's count, so a fraction is possible (Meta can attribute a part of one).
ALTER TABLE fin_ad_spends ADD COLUMN clicks INTEGER NOT NULL DEFAULT 0;
ALTER TABLE fin_ad_spends ADD COLUMN conversions REAL NOT NULL DEFAULT 0;
