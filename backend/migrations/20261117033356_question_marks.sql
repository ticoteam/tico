-- A ❓ on a bot's own chat message (backend/question_marks.py): the message needs one named person's reply. No task is
-- behind it and nothing is sent for it. `room_id` is the chat it sits in (the root conversation, as a thread's rail
-- names it); `task_id` the task whose chat that is, or '' in a direct chat. A bot has one open ❓ per chat: marking a
-- new message clears the old one. It is open until `cleared_at`: the person's reply (their own or by their Assistant,
-- `cleared_via`), the task ending, or a new ❓ from the same bot (`cleared_reason`).
CREATE TABLE IF NOT EXISTS question_marks (
  id TEXT PRIMARY KEY,
  message_id TEXT NOT NULL,
  conversation_id TEXT NOT NULL,
  room_id TEXT NOT NULL,
  task_id TEXT NOT NULL DEFAULT '',
  bot_id TEXT NOT NULL,
  person_id TEXT NOT NULL,
  line TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL,
  given_by_id TEXT,
  given_at TEXT,
  cleared_at TEXT,
  cleared_by_id TEXT,
  cleared_reason TEXT,
  cleared_message_id TEXT,
  cleared_via TEXT
);
CREATE UNIQUE INDEX IF NOT EXISTS question_marks_one_open ON question_marks(room_id, bot_id) WHERE cleared_at IS NULL;
CREATE INDEX IF NOT EXISTS question_marks_open_person ON question_marks(person_id, room_id) WHERE cleared_at IS NULL;
CREATE INDEX IF NOT EXISTS question_marks_open_task ON question_marks(task_id) WHERE cleared_at IS NULL;
CREATE INDEX IF NOT EXISTS question_marks_open_bot ON question_marks(bot_id) WHERE cleared_at IS NULL;
CREATE INDEX IF NOT EXISTS question_marks_message ON question_marks(message_id);
-- A task's replied marks: task_engine.progress_mark counts them on every ladder pass.
CREATE INDEX IF NOT EXISTS question_marks_replied_task ON question_marks(task_id) WHERE cleared_message_id IS NOT NULL;
-- A task that ends (done or closed, a merge closes it too) clears the ❓ on its chat, whichever path ended it. Reopening
-- it brings none back.
CREATE TRIGGER IF NOT EXISTS question_marks_task_ends AFTER UPDATE OF status ON tasks
WHEN NEW.status <> 'open' AND OLD.status = 'open'
BEGIN
  UPDATE question_marks SET cleared_at = coalesce(CASE WHEN NEW.status = 'done' THEN NEW.done_at ELSE NEW.closed_at END,
    strftime('%Y-%m-%dT%H:%M:%fZ','now')),
    cleared_reason = NEW.status,
    cleared_by_id = NEW.closed_by_id
  WHERE task_id = NEW.id AND cleared_at IS NULL;
END;
-- A deleted task, or a deleted marked message, takes its ❓ with it.
CREATE TRIGGER IF NOT EXISTS question_marks_task_deleted AFTER DELETE ON tasks
BEGIN
  UPDATE question_marks SET cleared_at = strftime('%Y-%m-%dT%H:%M:%fZ','now'), cleared_reason = 'deleted'
  WHERE task_id = OLD.id AND cleared_at IS NULL;
END;
CREATE TRIGGER IF NOT EXISTS question_marks_message_deleted AFTER UPDATE OF deleted_at ON messages
WHEN NEW.deleted_at IS NOT NULL AND OLD.deleted_at IS NULL
BEGIN
  UPDATE question_marks SET cleared_at = NEW.deleted_at, cleared_reason = 'deleted'
  WHERE message_id = NEW.id AND cleared_at IS NULL;
END;
CREATE TRIGGER IF NOT EXISTS question_marks_message_removed AFTER DELETE ON messages
BEGIN
  UPDATE question_marks SET cleared_at = strftime('%Y-%m-%dT%H:%M:%fZ','now'), cleared_reason = 'deleted'
  WHERE message_id = OLD.id AND cleared_at IS NULL;
END;
-- The open ❓ is mirrored on its message as refs.question {id, to}: every chat draws its glyph from the message, and the
-- change reaches open pages as the message's own (events.py, changes_messages_update).
CREATE TRIGGER IF NOT EXISTS question_marks_refs_open AFTER INSERT ON question_marks
WHEN NEW.cleared_at IS NULL
BEGIN
  UPDATE messages SET refs = json_set(CASE WHEN json_valid(refs) THEN refs ELSE '{}' END, '$.question',
    json_object('id', NEW.id, 'to', NEW.person_id))
  WHERE id = NEW.message_id;
END;
CREATE TRIGGER IF NOT EXISTS question_marks_refs_given AFTER UPDATE OF person_id ON question_marks
WHEN NEW.cleared_at IS NULL AND NEW.person_id IS NOT OLD.person_id
BEGIN
  UPDATE messages SET refs = json_set(CASE WHEN json_valid(refs) THEN refs ELSE '{}' END, '$.question',
    json_object('id', NEW.id, 'to', NEW.person_id))
  WHERE id = NEW.message_id;
END;
CREATE TRIGGER IF NOT EXISTS question_marks_refs_cleared AFTER UPDATE OF cleared_at ON question_marks
WHEN NEW.cleared_at IS NOT NULL AND OLD.cleared_at IS NULL
BEGIN
  UPDATE messages SET refs = json_remove(refs, '$.question')
  WHERE id = NEW.message_id AND json_valid(refs) AND json_extract(refs, '$.question.id') = NEW.id;
END;
