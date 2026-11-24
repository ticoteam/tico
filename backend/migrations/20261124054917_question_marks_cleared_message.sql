-- The askers a message answered (question_marks.cleared_askers) are found by the message that cleared their ❓, on
-- every person's post: by this index, never a scan of every ❓.
CREATE INDEX IF NOT EXISTS question_marks_cleared_message ON question_marks(cleared_message_id)
  WHERE cleared_message_id IS NOT NULL;
