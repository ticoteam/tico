-- Calls have other pending migrations that add columns. Store voice options independently of their order.
CREATE TABLE call_voice_options(
    call_id TEXT PRIMARY KEY NOT NULL REFERENCES calls(id) ON DELETE CASCADE,
    effort TEXT NOT NULL
);
ALTER TABLE live_meeting_bots ADD COLUMN voice_job TEXT;
ALTER TABLE live_meeting_turns ADD COLUMN voice_input TEXT;
ALTER TABLE live_meeting_turns ADD COLUMN voice_attempt TEXT;
ALTER TABLE live_meeting_turns ADD COLUMN voice_expires_at TEXT;
