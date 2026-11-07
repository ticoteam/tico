-- Skills a bot used that the company directory does not have: a daily count by name and bot, nothing else
-- (backend/skill_runtime.py). People's helpers forward directory skills only, so no person is ever counted here.
-- Kept 90 days by the skill usage rollup (backend/skill_usage.py).
CREATE TABLE IF NOT EXISTS unlisted_skill_usage_dailies (
  used_on TEXT NOT NULL,                     -- YYYY-MM-DD, UTC
  name TEXT NOT NULL,                        -- ^[a-z0-9][a-z0-9._-]{0,79}$, as the runner reported it
  bot_id TEXT NOT NULL,
  uses_count INTEGER NOT NULL,
  PRIMARY KEY(used_on, name, bot_id)
);
CREATE INDEX IF NOT EXISTS index_unlisted_skill_usage_dailies_on_name ON unlisted_skill_usage_dailies(name, used_on);

-- Remove and promote suggestions the maintenance run's Suggest step makes for Library › Skills
-- (backend/skill_suggestions.py). One row per subject; nothing here removes or submits anything.
CREATE TABLE IF NOT EXISTS skill_suggestions (
  id TEXT PRIMARY KEY,                       -- 'sks_' + random
  kind TEXT NOT NULL CHECK(kind IN ('remove', 'promote')),
  subject TEXT NOT NULL,                     -- remove: the install's id; promote: the skill's name
  name TEXT NOT NULL,                        -- the skill's name
  target_type TEXT NOT NULL,                 -- remove: the install's target; promote: 'Bot', the bot asked to submit it
  target_id TEXT NOT NULL,
  uses_count INTEGER NOT NULL DEFAULT 0,     -- promote: uses in the 30 days it was judged on
  bots_count INTEGER NOT NULL DEFAULT 0,     -- promote: bots that used it in those 30 days
  status TEXT NOT NULL DEFAULT 'open' CHECK(status IN ('open', 'acted', 'dismissed', 'expired')),
  closed_by_id TEXT,                         -- the admin who acted on it or dismissed it
  closed_at TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  UNIQUE(kind, subject)
);
CREATE INDEX IF NOT EXISTS index_skill_suggestions_on_status ON skill_suggestions(status, created_at);
