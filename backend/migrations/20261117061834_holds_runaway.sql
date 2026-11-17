-- One holds table (backend/holds.py; reports D1, PR 2 of "one holds system"): every reason a company, login, bot,
-- task, chat, job or automation may not start work. Its state is derived: waiting while until_at is ahead, probing
-- once it passed and probe_job_id is live, held while until_at is NULL, released once released_at is set. Released
-- rows stay as history. Only `runaway` (a bot over its run budget, backend/turn_budgets.py) is stored here so far.
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
-- Every bot_budget row moves: its open hold, and its last release (the run budget's count starts from it). A row that
-- is held again after a release keeps both. The older table is made empty first when it is gone, so running this again
-- changes nothing.
CREATE TABLE IF NOT EXISTS bot_budget(bot TEXT PRIMARY KEY, held_at TEXT, window TEXT, runs INTEGER, budget INTEGER,
  task_id TEXT, released_at TEXT, released_by TEXT);
-- bot_id names the held bot on every hold, a bot's own too (none for a bot that is gone).
INSERT OR IGNORE INTO holds(id, kind, holdable_type, holdable_id, bot_id, reason, details, upkeep_task_id,
  created_by_id, created_at, updated_at)
SELECT 'bot_budget-' || bb.bot, 'runaway', 'bot', bb.bot, b.id,
  'Tico stopped ' || coalesce(nullif(b.display_name, ''), bb.bot) || ': ' || coalesce(bb.runs, 0) ||
  ' runs in the last ' || coalesce(bb.window, 'hour') ||
  '. This looks like a runaway loop, not normal work. Check its last runs, then release it or raise its budget.',
  json_object('window', coalesce(bb.window, 'hour'), 'runs', bb.runs, 'budget', bb.budget), bb.task_id, 'keeper',
  bb.held_at, bb.held_at
FROM bot_budget bb LEFT JOIN bots b ON b.id = bb.bot
WHERE bb.held_at IS NOT NULL;
-- A released row's window, runs and budget are its last hold's; a row held again has the new hold's, so its release
-- keeps none. When that release's hold began is not kept: it is dated at the release.
INSERT OR IGNORE INTO holds(id, kind, holdable_type, holdable_id, bot_id, details, created_by_id, created_at,
  updated_at, released_at, released_by_id, released_how)
SELECT 'bot_budget-' || bb.bot || '-released', 'runaway', 'bot', bb.bot, b.id,
  CASE WHEN bb.held_at IS NULL THEN
    json_object('window', coalesce(bb.window, 'hour'), 'runs', bb.runs, 'budget', bb.budget)
    ELSE '{}' END,
  'keeper', bb.released_at, bb.released_at, bb.released_at, bb.released_by,
  CASE WHEN bb.released_by LIKE 'bot:%' THEN 'botops' ELSE 'person' END
FROM bot_budget bb LEFT JOIN bots b ON b.id = bb.bot
WHERE bb.released_at IS NOT NULL;
DROP TABLE bot_budget;
