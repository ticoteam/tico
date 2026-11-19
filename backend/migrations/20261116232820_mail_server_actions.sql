-- One-click unsubscribe requests the server sent (backend/mail_unsubscribe.py), one per mailbox and endpoint: claimed
-- before the request goes out, so a retry after an unclear answer never sends a second one. The endpoint itself (it
-- carries a recipient token) is kept only as a hash and its host.
CREATE TABLE IF NOT EXISTS mail_unsubscribes (
  key TEXT PRIMARY KEY,
  mailbox TEXT NOT NULL,
  host TEXT NOT NULL,
  status TEXT NOT NULL,
  http_status INTEGER,
  actor TEXT NOT NULL,
  created_at TEXT NOT NULL,
  finished_at TEXT
);

-- The Gmail drafts the server wrote, by the Message-ID Tico gave each (backend/mail_compose.py `ensure_draft`): claimed
-- before Gmail is asked, so two requests for the same draft at once make one, and the draft's id once it exists. Ids
-- only: the draft's text stays in Gmail.
CREATE TABLE IF NOT EXISTS mail_drafts (
  message_id TEXT PRIMARY KEY,
  mailbox TEXT NOT NULL,
  draft_id TEXT,
  message TEXT,
  thread TEXT,
  claimed_at TEXT NOT NULL
);
