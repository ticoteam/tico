-- The run guard's holds move into `holds` (reports D1, PR 9 of "one holds system"): kind `runaway` on the automation
-- (backend/automations.py, `_guard`).
-- Each automation_guard_holds row (one per automation) becomes one hold: its rule the reason, its arrivals and dropped
-- counts in the details, its Needs-you task the upkeep task, from its held_at. A row still holding (no cleared_at) is
-- open; a cleared one is released at cleared_at (where the guard starts counting again), by whoever last released or
-- dropped what it held (its latest automation.hold_released or automation.hold_dropped, `botops` when a bot, else
-- `person`), else by Tico. Its held runs stay `held`. The table is dropped after.
-- Ids name the kind and the thing, never a fresh id (H.new_id). The older table is made empty first when it is gone, so
-- running this again changes nothing.
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
CREATE TABLE IF NOT EXISTS automation_guard_holds(automation_id TEXT PRIMARY KEY, rule TEXT NOT NULL, held_at TEXT NOT NULL, arrivals INTEGER NOT NULL DEFAULT 0, dropped INTEGER NOT NULL DEFAULT 0, task_id TEXT, cleared_at TEXT);
INSERT OR IGNORE INTO holds(id, kind, holdable_type, holdable_id, reason, details, upkeep_task_id, created_by_id,
  created_at, updated_at, released_at, released_by_id, released_how)
SELECT 'runaway:automation:' || g.automation_id, 'runaway', 'automation', g.automation_id, g.rule,
  json_object('arrivals', g.arrivals, 'dropped', g.dropped), g.task_id, 'keeper', g.held_at,
  coalesce(g.cleared_at, g.held_at), g.cleared_at,
  CASE WHEN g.cleared_at IS NOT NULL THEN coalesce(g.by, 'keeper') END,
  CASE WHEN g.cleared_at IS NULL THEN NULL WHEN g.by LIKE 'bot:%' THEN 'botops' ELSE 'person' END
FROM (
  SELECT h.*, (
    SELECT e.actor FROM events e WHERE e.action IN ('automation.hold_released', 'automation.hold_dropped')
      AND e.target = h.automation_id ORDER BY e.created_at DESC LIMIT 1
  ) AS by
  FROM automation_guard_holds h
) g;
DROP TABLE automation_guard_holds;
