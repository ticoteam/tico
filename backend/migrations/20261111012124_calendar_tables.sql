-- Tico's own calendar (backend/calendar_store.py, docs/dev/calendar-contract.md "Tables"). A calendar belongs to a
-- teammate (made on first use) or is shared (an admin makes it). Its home is Tico or a pointer to a Google calendar.
CREATE TABLE IF NOT EXISTS calendars (
  id TEXT PRIMARY KEY,
  owner_type TEXT NOT NULL CHECK (owner_type IN ('human','bot','shared')),
  owner_id TEXT,
  name TEXT NOT NULL,
  home TEXT NOT NULL DEFAULT 'tico' CHECK (home IN ('tico','google')),
  google_calendar_id TEXT,
  time_zone TEXT,
  created_by TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS calendars_owner ON calendars(owner_type, owner_id) WHERE owner_type <> 'shared';

-- Events follow RFC 5545: a series stores its rule, never its instances; times are wall time plus an IANA zone, with
-- the first instance's UTC kept for range queries; all-day entries are dates. An exception row (one moved or cancelled
-- instance) points at its series and is keyed by the instance's original start, like Google's recurringEventId +
-- originalStartTime. Rows are cancelled, never deleted, until the 90-day purge.
CREATE TABLE IF NOT EXISTS calendar_events (
  id TEXT PRIMARY KEY,
  calendar_id TEXT NOT NULL REFERENCES calendars(id) ON DELETE CASCADE,
  uid TEXT NOT NULL,
  title TEXT NOT NULL,
  notes TEXT,
  location TEXT,
  meeting_url TEXT,
  all_day INTEGER NOT NULL DEFAULT 0 CHECK (all_day IN (0, 1)),
  starts_on TEXT,
  ends_on TEXT,
  starts_at_local TEXT,
  ends_at_local TEXT,
  time_zone TEXT,
  starts_at TEXT NOT NULL,
  ends_at TEXT NOT NULL,
  rrule TEXT,
  until_at TEXT,
  recurring_event_id TEXT REFERENCES calendar_events(id) ON DELETE CASCADE,
  original_starts_at TEXT,
  status TEXT NOT NULL DEFAULT 'confirmed' CHECK (status IN ('confirmed','tentative','cancelled')),
  visibility TEXT NOT NULL DEFAULT 'default' CHECK (visibility IN ('default','private')),
  sequence INTEGER NOT NULL DEFAULT 0,
  created_by TEXT,
  task_id TEXT,
  thread_id TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  CHECK ((all_day = 1) = (starts_on IS NOT NULL AND ends_on IS NOT NULL)),
  CHECK (all_day = 1 OR (starts_at_local IS NOT NULL AND ends_at_local IS NOT NULL AND time_zone IS NOT NULL)),
  CHECK ((recurring_event_id IS NULL) = (original_starts_at IS NULL)),
  CHECK (recurring_event_id IS NULL OR rrule IS NULL)
);
CREATE INDEX IF NOT EXISTS calendar_events_calendar_starts ON calendar_events(calendar_id, starts_at);
CREATE INDEX IF NOT EXISTS calendar_events_calendar_until ON calendar_events(calendar_id, until_at);
CREATE UNIQUE INDEX IF NOT EXISTS calendar_events_exception
  ON calendar_events(recurring_event_id, original_starts_at);
CREATE INDEX IF NOT EXISTS calendar_events_purge ON calendar_events(status, updated_at);

CREATE TABLE IF NOT EXISTS calendar_event_attendees (
  id TEXT PRIMARY KEY,
  calendar_event_id TEXT NOT NULL REFERENCES calendar_events(id) ON DELETE CASCADE,
  attendee_type TEXT NOT NULL CHECK (attendee_type IN ('human','bot','external')),
  attendee_id TEXT,
  email TEXT,
  response TEXT NOT NULL DEFAULT 'needs_action'
    CHECK (response IN ('needs_action','accepted','tentative','declined')),
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  CHECK ((attendee_type = 'external') = (attendee_id IS NULL)),
  CHECK (attendee_type <> 'external' OR email IS NOT NULL)
);
CREATE INDEX IF NOT EXISTS calendar_event_attendees_event ON calendar_event_attendees(calendar_event_id);
CREATE INDEX IF NOT EXISTS calendar_event_attendees_who ON calendar_event_attendees(attendee_type, attendee_id);

-- Time away for people with nothing connected; both dates are days away (ends_on inclusive).
CREATE TABLE IF NOT EXISTS absences (
  id TEXT PRIMARY KEY,
  human_id TEXT NOT NULL,
  starts_on TEXT NOT NULL,
  ends_on TEXT NOT NULL,
  note TEXT,
  created_by TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  CHECK (ends_on >= starts_on)
);
CREATE INDEX IF NOT EXISTS absences_human ON absences(human_id, starts_on);

-- Levels on a Tico calendar, the mailbox_accesses pattern: everyone reads; these add read_write.
CREATE TABLE IF NOT EXISTS calendar_accesses (
  id TEXT PRIMARY KEY,
  calendar_id TEXT NOT NULL REFERENCES calendars(id) ON DELETE CASCADE,
  grantee_type TEXT NOT NULL CHECK (grantee_type IN ('human','bot','group')),
  grantee_id TEXT NOT NULL,
  level TEXT NOT NULL CHECK (level IN ('read','read_write')),
  created_by TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  UNIQUE (calendar_id, grantee_type, grantee_id)
);

-- What an event led to (a recorded meeting, a thread, a task, a bot), for a Tico event or a Google one by iCalUID.
CREATE TABLE IF NOT EXISTS calendar_event_links (
  id TEXT PRIMARY KEY,
  google_calendar_id TEXT,
  ical_uid TEXT,
  tico_event_id TEXT,
  original_starts_at TEXT,
  link_type TEXT NOT NULL CHECK (link_type IN ('meeting','thread','task','bot')),
  link_id TEXT NOT NULL,
  created_at TEXT NOT NULL,
  CHECK ((tico_event_id IS NULL) <> (ical_uid IS NULL))
);
CREATE INDEX IF NOT EXISTS calendar_event_links_tico ON calendar_event_links(tico_event_id, original_starts_at);
CREATE INDEX IF NOT EXISTS calendar_event_links_google ON calendar_event_links(ical_uid, original_starts_at);
CREATE INDEX IF NOT EXISTS calendar_event_links_target ON calendar_event_links(link_type, link_id);

-- Search over title, notes and location. The index keeps its own copy keyed by calendar_event_search_keys.id, not an
-- external-content index on calendar_events' implicit rowid: a VACUUM (backend/backup.py) may renumber that rowid,
-- which would point the index at the wrong events. Triggers keep it in step with every write, the purge included.
CREATE TABLE IF NOT EXISTS calendar_event_search_keys (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  calendar_event_id TEXT NOT NULL UNIQUE
);
CREATE VIRTUAL TABLE IF NOT EXISTS calendar_event_search USING fts5(
  title, notes, location, tokenize = 'porter unicode61 remove_diacritics 1'
);
CREATE TRIGGER IF NOT EXISTS calendar_event_search_insert AFTER INSERT ON calendar_events BEGIN
  INSERT OR IGNORE INTO calendar_event_search_keys(calendar_event_id) VALUES (NEW.id);
  INSERT INTO calendar_event_search(rowid, title, notes, location)
    SELECT k.id, NEW.title, coalesce(NEW.notes, ''), coalesce(NEW.location, '')
    FROM calendar_event_search_keys k WHERE k.calendar_event_id = NEW.id;
END;
CREATE TRIGGER IF NOT EXISTS calendar_event_search_update AFTER UPDATE OF title, notes, location ON calendar_events
BEGIN
  DELETE FROM calendar_event_search
    WHERE rowid = (SELECT id FROM calendar_event_search_keys WHERE calendar_event_id = OLD.id);
  INSERT INTO calendar_event_search(rowid, title, notes, location)
    SELECT k.id, NEW.title, coalesce(NEW.notes, ''), coalesce(NEW.location, '')
    FROM calendar_event_search_keys k WHERE k.calendar_event_id = NEW.id;
END;
CREATE TRIGGER IF NOT EXISTS calendar_event_search_delete AFTER DELETE ON calendar_events BEGIN
  DELETE FROM calendar_event_search
    WHERE rowid = (SELECT id FROM calendar_event_search_keys WHERE calendar_event_id = OLD.id);
  DELETE FROM calendar_event_search_keys WHERE calendar_event_id = OLD.id;
END;

-- A person's zone and working hours (calendar settings, free/busy). Null means the default: the company zone,
-- 08:00-18:00, Monday to Friday.
ALTER TABLE humans ADD COLUMN time_zone TEXT;
ALTER TABLE humans ADD COLUMN work_starts TEXT;
ALTER TABLE humans ADD COLUMN work_ends TEXT;
ALTER TABLE humans ADD COLUMN work_days TEXT;
-- A person's "Record calls I join" (Calendar, spec 12.5): null means on.
ALTER TABLE humans ADD COLUMN record_on_join INTEGER CHECK (record_on_join IN (0, 1));
