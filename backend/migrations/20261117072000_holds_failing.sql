-- Failed runs move into `holds` (backend/run_failures.py; reports D1, PR 3 of "one holds system"): a `failing` hold
-- on the bot is open while its fast failures in a row are on record. Its streak is their count; until_at is the wait
-- after the last one; a held bot has no until_at, and probe_at is when its next try is due. details keep the last
-- failure's class, error and time, and when the bot was held.
-- Every run_backoff row moves: its open hold or wait, and its last release. Ids name the row's kind before the bot, so
-- no two bots' rows share one. The older table is made empty first when it is gone, so running this again changes
-- nothing.
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
