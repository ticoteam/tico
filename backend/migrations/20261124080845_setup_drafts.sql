-- A task made from a draft an agent wrote in setup (backend/setup_drafts.py): its agent never runs on it while it waits
-- on the person's Send, and never on Gateway credit once it may.
CREATE TABLE IF NOT EXISTS setup_drafts (
  task_id TEXT PRIMARY KEY,
  bot_id TEXT NOT NULL,
  person_id TEXT NOT NULL,
  kind TEXT NOT NULL,
  status TEXT NOT NULL CHECK (status IN ('waiting', 'approved')),
  created_at TEXT NOT NULL,
  decided_at TEXT,
  decided_by_id TEXT
);
