-- What a bot asked of Slack through the API (backend/slack_bots.py): a channel's history or a shared file. The API
-- process holds no Slack token, so it writes the request here and waits; the Slack gateway, which holds the token,
-- answers it (backend/slack_requests.py) and the API deletes the row once read. A file's bytes are not kept here: the
-- gateway writes them beside the database for the API to stream and delete.
CREATE TABLE IF NOT EXISTS slack_requests (
  id TEXT PRIMARY KEY,
  kind TEXT NOT NULL CHECK (kind IN ('history','file')),
  bot TEXT NOT NULL,
  params TEXT NOT NULL DEFAULT '{}',
  state TEXT NOT NULL DEFAULT 'pending' CHECK (state IN ('pending','running','done','failed')),
  result TEXT,
  error TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
-- The gateway's claim reads the oldest waiting requests.
CREATE INDEX IF NOT EXISTS slack_requests_pending ON slack_requests(created_at) WHERE state='pending';
