-- Pauses move into `holds` (reports D1, PR 7 of "one holds system"): kind `paused` on the company and on a bot
-- (backend/company_pause.py).
-- 1. The company's pause, the registry_metadata row `company_pause` {"paused_by", "paused_at"}, becomes an open hold
--    on the company (holdable_id ''), by its pauser and from its time. A row with no time was read as no pause and is
--    dropped. The row is deleted after.
-- 2. Each bot whose bots.state is 'paused' gets an open hold on it, by whoever paused it last (its latest bot.pause,
--    when that is later than its latest bot.resume) and from then, else by Tico from now. Its bots.state stays
--    'paused': the lifecycle gate every other check reads, kept in step with the hold.
-- Ids name the kind and the thing, never a fresh id (H.new_id). Running this again changes nothing: the company row is
-- gone, and a bot's open hold is kept (holds_open).
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
INSERT OR IGNORE INTO holds(id, kind, holdable_type, holdable_id, created_by_id, created_at, updated_at)
SELECT 'company_pause', 'paused', 'company', '', coalesce(nullif(json_extract(value, '$.paused_by'), ''), 'keeper'),
  json_extract(value, '$.paused_at'), json_extract(value, '$.paused_at')
FROM registry_metadata
WHERE key = 'company_pause' AND json_valid(value) AND json_type(value) = 'object'
  AND coalesce(json_extract(value, '$.paused_at'), '') <> '';
DELETE FROM registry_metadata WHERE key = 'company_pause';
INSERT OR IGNORE INTO holds(id, kind, holdable_type, holdable_id, bot_id, created_by_id, created_at, updated_at)
SELECT 'paused:bot:' || p.bot, 'paused', 'bot', p.bot, p.bot, coalesce(e.actor, 'keeper'),
  coalesce(e.created_at, strftime('%Y-%m-%dT%H:%M:%f000Z', 'now')),
  coalesce(e.created_at, strftime('%Y-%m-%dT%H:%M:%f000Z', 'now'))
FROM (
  SELECT b.id AS bot, (
    SELECT x.id FROM events x WHERE x.target = b.id AND x.action IN ('bot.pause', 'bot.resume')
    ORDER BY x.created_at DESC, x.rowid DESC LIMIT 1
  ) AS last
  FROM bots b WHERE b.state = 'paused'
) p LEFT JOIN events e ON e.id = p.last AND e.action = 'bot.pause';
