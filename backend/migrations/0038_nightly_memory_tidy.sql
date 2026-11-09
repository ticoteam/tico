CREATE TABLE IF NOT EXISTS memory_tidy_passes(
 id TEXT PRIMARY KEY, night TEXT NOT NULL, bot TEXT NOT NULL, model TEXT NOT NULL,
 started_at TEXT NOT NULL, deadline TEXT NOT NULL, status TEXT NOT NULL,
 reason TEXT NOT NULL DEFAULT '', parent_task_id TEXT, check_task_id TEXT,
 baseline TEXT NOT NULL DEFAULT '', cost_usd REAL NOT NULL DEFAULT 0,
 UNIQUE(night,bot));
CREATE INDEX IF NOT EXISTS memory_tidy_passes_bot ON memory_tidy_passes(bot,started_at);

CREATE TABLE IF NOT EXISTS memory_tidy_packets(
 attempt_id TEXT PRIMARY KEY, pass_id TEXT NOT NULL UNIQUE, memory_commit TEXT NOT NULL,
 snapshot TEXT NOT NULL, before_digest TEXT NOT NULL, after_digest TEXT,
 since TEXT, previous_learnings TEXT, proposal TEXT, conflicts INTEGER,
 flags TEXT NOT NULL DEFAULT '[]', stage TEXT NOT NULL DEFAULT 'prepared');
