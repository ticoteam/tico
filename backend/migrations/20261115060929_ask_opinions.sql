-- Flow's opinion on each ask waiting on a person (backend/flow_opinions.py): one row per ask (its visit and
-- requirement), shared by everyone who has the ask on their Needs you. `ask_version` is the ask as it was written when
-- the opinion was asked for; `started_at` is the claim of whoever is working it out, `written_at` when it was written.
-- What the person pressed in the end is the ask's own answers row (same visit and requirement).
CREATE TABLE IF NOT EXISTS ask_opinions (
  visit_id TEXT NOT NULL,
  requirement_id TEXT NOT NULL,
  task_id TEXT NOT NULL,
  ask_version TEXT NOT NULL,
  state TEXT NOT NULL CHECK (state IN ('pending', 'ready', 'none')),
  pick TEXT NOT NULL DEFAULT '',
  line TEXT NOT NULL DEFAULT '',
  note TEXT NOT NULL DEFAULT '',
  score INTEGER CHECK (score IS NULL OR score BETWEEN 0 AND 100),
  model TEXT,
  error TEXT,
  started_at TEXT NOT NULL,
  written_at TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  PRIMARY KEY (visit_id, requirement_id)
);
CREATE INDEX IF NOT EXISTS ask_opinions_task ON ask_opinions(task_id);
