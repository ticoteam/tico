-- Login holds move into `holds` (reports D1, PR 6 of "one holds system"): each `login-hold:<login>` row of
-- registry_metadata becomes an open `usage_limit` hold on that login (backend/login_holds.py). Its end, streak and probe
-- go in the row's columns; the rest of the saved hold (whose limit, its reset and notice, the probe's window it cut
-- short) stays as its details. A value that is not a JSON object was read as no hold, and is dropped.
-- Ids are the old key: one per login, and never a fresh id (H.new_id). The keys are deleted after, so running this
-- again changes nothing.
-- The holds table as 20261117061834_holds_runaway makes it, word for word: pending migrations run in any order.
CREATE TABLE IF NOT EXISTS holds (
  id TEXT PRIMARY KEY,
  kind TEXT NOT NULL CHECK (kind IN ('paused','failing','runaway','claim_error','cant_start','quarantine','usage_limit')),
  holdable_type TEXT NOT NULL CHECK (holdable_type IN ('company','login','bot','task','conversation','job','automation')),
  holdable_id TEXT NOT NULL,
  bot_id TEXT REFERENCES bots(id),
  reason TEXT NOT NULL DEFAULT '',
  details TEXT NOT NULL DEFAULT '{}',
  streak INTEGER NOT NULL DEFAULT 0,
  until_at TEXT,
  probe_at TEXT, probe_job_id TEXT,
  upkeep_task_id TEXT,
  created_by_id TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
  released_at TEXT, released_by_id TEXT,
  released_how TEXT CHECK (released_how IN ('expired','probe_passed','person','botops','raised','moot','superseded'))
);
-- At most one open hold of a kind on one thing (holds.put upserts on it).
CREATE UNIQUE INDEX IF NOT EXISTS holds_open ON holds(kind, holdable_type, holdable_id, coalesce(bot_id,''))
  WHERE released_at IS NULL;
CREATE INDEX IF NOT EXISTS holds_bot ON holds(bot_id) WHERE released_at IS NULL;
-- The last release of a kind on one thing: a run budget counts runs from it, on every claim of a bot's own work.
CREATE INDEX IF NOT EXISTS holds_released ON holds(holdable_type, holdable_id, kind, released_at)
  WHERE released_at IS NOT NULL;
INSERT OR IGNORE INTO holds(id, kind, holdable_type, holdable_id, details, streak, until_at, probe_at, probe_job_id,
  created_by_id, created_at, updated_at)
SELECT key, 'usage_limit', 'login', substr(key, 12),
  json_remove(value, '$.until', '$.streak', '$.probe_job', '$.probe_at'),
  CAST(coalesce(json_extract(value, '$.streak'), 0) AS INTEGER), json_extract(value, '$.until'),
  json_extract(value, '$.probe_at'), json_extract(value, '$.probe_job'), 'keeper',
  coalesce(json_extract(value, '$.since'), strftime('%Y-%m-%dT%H:%M:%f000Z', 'now')),
  strftime('%Y-%m-%dT%H:%M:%f000Z', 'now')
FROM registry_metadata
WHERE key >= 'login-hold:' AND key < 'login-hold;' AND json_valid(value) AND json_type(value) = 'object';
DELETE FROM registry_metadata WHERE key >= 'login-hold:' AND key < 'login-hold;';
