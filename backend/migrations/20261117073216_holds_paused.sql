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
