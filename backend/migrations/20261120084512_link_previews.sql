-- Link previews (backend/link_preview.py): what the server read from an outside web page, one row per address, so one
-- fetch serves everyone for a day. id is the sha256 of the address; found is 0 when the page gave no preview (kept a
-- shorter while so a dead link is not fetched on every draw). image is the page's picture made small and re-encoded as
-- JPEG here, served from /api/v2/link-preview/images/<id>, and image_bytes its size, which the pictures' total budget
-- counts (the oldest go first). A row past expires_at is never served and is deleted.
CREATE TABLE IF NOT EXISTS link_previews (
  id TEXT PRIMARY KEY,
  url TEXT NOT NULL,
  found INTEGER NOT NULL DEFAULT 0 CHECK (found IN (0, 1)),
  title TEXT,
  site_name TEXT,
  description TEXT,
  image BLOB,
  image_bytes INTEGER NOT NULL DEFAULT 0,
  image_width INTEGER,
  image_height INTEGER,
  created_at TEXT NOT NULL,
  expires_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS link_previews_expires ON link_previews(expires_at);
CREATE INDEX IF NOT EXISTS link_previews_created ON link_previews(created_at, id);
