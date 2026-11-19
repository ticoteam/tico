-- Page history (backend/page_history.py): the pages a person opened in the app, newest first, kept 30 days for Tico.
-- actor_id is whose history it is (always a person); via is 'assistant' when Tico opened the page for them. page is the
-- app's address path (/tasks/engineering/412); title is the one line the app shows for it; subject_type and subject_id
-- name the record the page shows, when it shows one.
CREATE TABLE IF NOT EXISTS page_visits (
  id TEXT PRIMARY KEY,
  actor_id TEXT NOT NULL,
  page TEXT NOT NULL,
  title TEXT NOT NULL DEFAULT '',
  subject_type TEXT,
  subject_id TEXT,
  via TEXT NOT NULL DEFAULT '' CHECK (via IN ('', 'assistant')),
  created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS page_visits_actor_created ON page_visits(actor_id, created_at, id);
