CREATE TABLE call_follow_ups (
  task_id TEXT PRIMARY KEY REFERENCES tasks(id) ON DELETE CASCADE,
  call_id TEXT NOT NULL REFERENCES calls(id) ON DELETE CASCADE,
  bot_id TEXT NOT NULL,
  message_id TEXT REFERENCES messages(id) ON DELETE SET NULL,
  speech_pending INTEGER NOT NULL DEFAULT 0 CHECK (speech_pending IN (0, 1)),
  returned_state TEXT CHECK (returned_state IN ('done', 'closed', 'failed'))
);
CREATE INDEX call_follow_ups_call ON call_follow_ups(call_id);
CREATE INDEX call_follow_ups_pending ON call_follow_ups(task_id) WHERE returned_state IS NULL;
CREATE INDEX call_follow_ups_speech ON call_follow_ups(call_id,message_id) WHERE speech_pending=1;
