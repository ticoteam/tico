-- Access requests (backend/access_requests.py): a bot asks for a level on a tool or a GitHub repository that exists.
-- One open request per (subject, resource, level); a repeat counts on it. A request closes itself the moment the
-- access exists, however it was granted. `kind` tags it for the weekly counts (requests to people, share approved
-- at once, time to grant); `cards` maps each person its card went to onto that card's message.
CREATE TABLE IF NOT EXISTS access_requests (
  id TEXT PRIMARY KEY,
  subject TEXT NOT NULL,
  resource_type TEXT NOT NULL CHECK (resource_type IN ('tool','repository')),
  resource_id TEXT NOT NULL,
  resource_name TEXT NOT NULL DEFAULT '',
  level TEXT NOT NULL CHECK (level IN ('read','read_write')),
  kind TEXT NOT NULL,
  reason TEXT NOT NULL DEFAULT '',
  requested_by TEXT NOT NULL,
  task_id TEXT,
  state TEXT NOT NULL DEFAULT 'open' CHECK (state IN ('open','granted','declined','withdrawn','revoked')),
  auto_approved INTEGER NOT NULL DEFAULT 0 CHECK (auto_approved IN (0, 1)),
  approvers TEXT NOT NULL DEFAULT '[]',
  cards TEXT NOT NULL DEFAULT '{}',
  repeats INTEGER NOT NULL DEFAULT 0,
  resolved_by TEXT,
  resolved_via TEXT,
  created_at TEXT NOT NULL,
  resolved_at TEXT
);
CREATE UNIQUE INDEX IF NOT EXISTS index_access_requests_on_open
  ON access_requests(subject, resource_type, resource_id, level) WHERE state = 'open';
CREATE INDEX IF NOT EXISTS index_access_requests_on_created_at ON access_requests(created_at);
