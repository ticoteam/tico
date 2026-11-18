-- Failed runs move into `holds` (backend/run_failures.py; reports D1, PR 3 of "one holds system"): a `failing` hold
-- on the bot is open while its fast failures in a row are on record. Its streak is their count; until_at is the wait
-- after the last one; a held bot has no until_at, and probe_at is when its next try is due. details keep the last
-- failure's class, error and time, and when the bot was held.
-- Every run_backoff row moves: its open hold or wait, and its last release. Ids name the row's kind before the bot, so
-- no two bots' rows share one. The older table is made empty first when it is gone, so running this again changes
-- nothing.
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
CREATE TABLE IF NOT EXISTS run_backoff(bot TEXT PRIMARY KEY, fails INTEGER NOT NULL DEFAULT 0, wait_until TEXT,
  error_class TEXT, error TEXT, last_failed_at TEXT, held_at TEXT, task_id TEXT, released_at TEXT, released_by TEXT);
-- A held bot's next try: the time kept, else 30 minutes after the hold began (a hold from before tries had none). A
-- bot that only waits keeps its wait; one with no wait on record has waited it out. bot_id names the held bot (none
-- for a bot that is gone). The reason is the sentence every page shows for a held bot.
INSERT OR IGNORE INTO holds(id, kind, holdable_type, holdable_id, bot_id, reason, details, streak, until_at, probe_at,
  upkeep_task_id, created_by_id, created_at, updated_at)
SELECT 'run_backoff:open:' || rb.bot, 'failing', 'bot', rb.bot, b.id,
  CASE WHEN rb.held_at IS NULL THEN '' ELSE
    'Tico stopped ' || coalesce(nullif(b.display_name, ''), rb.bot) || ': its last ' || rb.fails ||
    ' runs failed within a minute (' ||
    CASE rb.error_class
      WHEN 'usage_limit' THEN 'a provider usage limit'
      WHEN 'prompt_too_long' THEN 'the session grew too large to send'
      WHEN 'sign_in' THEN 'the AI sign-in was refused'
      WHEN 'gateway' THEN 'the Tico AI Gateway refused the run'
      ELSE 'an error before any work' END ||
    '). Tico tries one run on its own at growing intervals and releases it when one completes. Check its last runs, '
    || 'or release it now.' END,
  json_object('error_class', rb.error_class, 'error', rb.error, 'last_failed_at', rb.last_failed_at,
    'held_at', rb.held_at),
  rb.fails,
  CASE WHEN rb.held_at IS NULL THEN coalesce(rb.wait_until, strftime('%Y-%m-%dT%H:%M:%f000Z', 'now')) END,
  CASE WHEN rb.held_at IS NOT NULL THEN
    coalesce(rb.wait_until, strftime('%Y-%m-%dT%H:%M:%f000Z', rb.held_at, '+1800 seconds')) END,
  rb.task_id, 'keeper',
  coalesce(rb.held_at, rb.last_failed_at, strftime('%Y-%m-%dT%H:%M:%f000Z', 'now')),
  coalesce(rb.last_failed_at, rb.held_at, strftime('%Y-%m-%dT%H:%M:%f000Z', 'now'))
FROM run_backoff rb LEFT JOIN bots b ON b.id = rb.bot
WHERE rb.fails > 0 OR rb.held_at IS NOT NULL;
-- The last release. Its failures are not kept: the row's error is its latest failure's, which may be a later streak's.
INSERT OR IGNORE INTO holds(id, kind, holdable_type, holdable_id, bot_id, created_by_id, created_at, updated_at,
  released_at, released_by_id, released_how)
SELECT 'run_backoff:released:' || rb.bot, 'failing', 'bot', rb.bot, b.id, 'keeper', rb.released_at, rb.released_at,
  rb.released_at, rb.released_by, CASE WHEN rb.released_by LIKE 'bot:%' THEN 'botops' ELSE 'person' END
FROM run_backoff rb LEFT JOIN bots b ON b.id = rb.bot
WHERE rb.released_at IS NOT NULL;
DROP TABLE run_backoff;
