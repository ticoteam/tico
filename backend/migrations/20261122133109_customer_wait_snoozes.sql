ALTER TABLE snoozes ADD COLUMN wait_thread_id TEXT;
ALTER TABLE snoozes ADD COLUMN wait_sender TEXT;
ALTER TABLE snoozes ADD COLUMN wait_after_seq INTEGER;
CREATE INDEX snoozes_wait_thread ON snoozes(wait_thread_id) WHERE ended_at IS NULL;
