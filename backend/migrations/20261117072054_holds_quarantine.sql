-- Quarantines move into the holds table (backend/holds.py; reports D1, PR 5 of "one holds system"): a bot-wide one is
-- an open `quarantine` hold on the bot, and one on a task or chat (backend/task_quarantine.py) a hold on that task or
-- conversation naming the held bot. A refusal count's hold waits until its until_at (the hour's cooldown); an escape's
-- has none and waits for a person. bots.state stays `quarantined` while a bot-wide hold is open (holds.put and
-- holds.release keep it so), so every reader of it is unchanged and the bots table is not touched here.
-- Ids name the row's kind before its subject, so no two rows share one. Running this again changes nothing.
-- The holds table as 20261117061834_holds_runaway makes it, for a company that applies the two in either order.
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
--
-- Each quarantined bot: an open hold whose reason, review and refusal are its latest quarantine event's.
INSERT OR IGNORE INTO holds(id, kind, holdable_type, holdable_id, bot_id, reason, details, until_at, created_by_id,
  created_at, updated_at)
SELECT 'bots:quarantined:' || b.id, 'quarantine', 'bot', b.id, b.id, coalesce(json_extract(e.detail, '$.reason'), ''),
  CASE WHEN json_valid(e.detail) THEN json_remove(e.detail, '$.reason') ELSE '{}' END,
  -- A refusal count (hubdb.COUNT_QUARANTINE) lifts itself an hour after it began.
  CASE WHEN json_extract(e.detail, '$.reason') GLOB '[0-9]* refused writes today'
    OR json_extract(e.detail, '$.reason') GLOB '[0-9]* repeated refusals today'
    OR json_extract(e.detail, '$.reason') GLOB '[0-9]* refusals today'
    THEN strftime('%Y-%m-%dT%H:%M:%S', e.created_at, '+3600 seconds') || substr(e.created_at, 20) END,
  'keeper', coalesce(e.created_at, strftime('%Y-%m-%dT%H:%M:%fZ')), coalesce(e.created_at, strftime('%Y-%m-%dT%H:%M:%fZ'))
FROM bots b LEFT JOIN events e ON e.rowid = (SELECT rowid FROM events WHERE action = 'quarantine'
  AND target = 'bot:' || b.id ORDER BY created_at DESC, rowid DESC LIMIT 1)
WHERE b.state = 'quarantined';
-- Every hold on one task or chat, open and released. Who released one was not kept: a release after its cooldown is
-- the cooldown's (expired), any earlier one a person's.
CREATE TABLE IF NOT EXISTS quarantine_holds(id INTEGER PRIMARY KEY, bot_id TEXT NOT NULL, task_id TEXT,
  conversation_id TEXT, reason TEXT NOT NULL, refusal TEXT NOT NULL, created_at TEXT NOT NULL, released_at TEXT);
INSERT OR IGNORE INTO holds(id, kind, holdable_type, holdable_id, bot_id, reason, details, until_at, created_by_id,
  created_at, updated_at, released_at, released_by_id, released_how)
SELECT 'quarantine_holds:' || q.id, 'quarantine', CASE WHEN q.task_id IS NOT NULL THEN 'task' ELSE 'conversation' END,
  coalesce(q.task_id, q.conversation_id), q.bot_id, q.reason,
  json_object('refusal', CASE WHEN json_valid(q.refusal) THEN json(q.refusal) ELSE q.refusal END), q.until_at,
  'keeper', q.created_at, coalesce(q.released_at, q.created_at), q.released_at,
  CASE WHEN q.released_at IS NULL THEN NULL WHEN q.released_at >= q.until_at THEN 'keeper' END,
  CASE WHEN q.released_at IS NULL THEN NULL WHEN q.released_at >= q.until_at THEN 'expired' ELSE 'person' END
FROM (SELECT *, strftime('%Y-%m-%dT%H:%M:%S', created_at, '+3600 seconds') || substr(created_at, 20) AS until_at
  FROM quarantine_holds) q;
DROP TABLE quarantine_holds;
