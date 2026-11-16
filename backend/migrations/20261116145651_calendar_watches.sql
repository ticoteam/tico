-- The Google calendar watcher (backend/gcal_watch.py): one row per watched Google calendar, read as `mailbox`, with
-- Google's sync token (what changed since the last check), when it was last checked and its last error. A calendar
-- whose row is new is listed once to learn what is on it, which announces nothing.
CREATE TABLE IF NOT EXISTS calendar_watches (
  calendar_id TEXT PRIMARY KEY,
  mailbox TEXT NOT NULL,
  sync_token TEXT,
  checked_at TEXT,
  check_error TEXT,
  watching_since_at TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS calendar_watches_checked ON calendar_watches(checked_at);

-- What the watcher last saw of each event: its sequence and whether it was cancelled, so a change is announced once
-- (`calendar.event.*`, deduped on calendar, event and sequence) and a full list after an expired token can tell what
-- went. `own_write` marks an event Tico itself just wrote, already announced: its next sighting is taken quietly.
-- No title, notes, place or attendees are kept.
CREATE TABLE IF NOT EXISTS calendar_watch_events (
  id INTEGER PRIMARY KEY,
  calendar_id TEXT NOT NULL,
  event_id TEXT NOT NULL,
  series_id TEXT,
  sequence INTEGER NOT NULL DEFAULT 0,
  status TEXT NOT NULL DEFAULT 'confirmed' CHECK (status IN ('confirmed', 'cancelled')),
  repeats INTEGER NOT NULL DEFAULT 0,
  ends_at TEXT,
  own_write INTEGER NOT NULL DEFAULT 0,
  updated_at TEXT NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS calendar_watch_events_event ON calendar_watch_events(calendar_id, event_id);
