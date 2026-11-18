-- Make the schema change here; the migration runner owns the transaction.
ALTER TABLE calls ADD COLUMN voice_effort TEXT;
ALTER TABLE live_meeting_bots ADD COLUMN voice_job TEXT;
ALTER TABLE live_meeting_turns ADD COLUMN voice_input TEXT;
ALTER TABLE live_meeting_turns ADD COLUMN voice_attempt TEXT;
ALTER TABLE live_meeting_turns ADD COLUMN voice_expires_at TEXT;
