-- A stale Recorder proposal must not overwrite an item edited on the meeting.
ALTER TABLE meeting_items ADD COLUMN version INTEGER NOT NULL DEFAULT 1;
