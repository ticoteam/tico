-- What a plain task-thread message's provenance reads can change after it is written: its own refs, parent or thread,
-- a message it replies to or answers, or the run that wrote it (a new or moved input, another task or job, a carried
-- task). Each change is noted here and checked again before the write commits (threads.restamp_dirty, called by
-- Store.transaction); it can only make a message restricted.
CREATE TABLE messages_plain_dirty(message_id TEXT PRIMARY KEY) WITHOUT ROWID;
CREATE TABLE messages_plain_runs(attempt_id TEXT PRIMARY KEY) WITHOUT ROWID;
CREATE TRIGGER messages_plain_update AFTER UPDATE OF refs, in_reply_to, conversation_id ON messages
BEGIN INSERT OR IGNORE INTO messages_plain_dirty VALUES(NEW.id); END;
CREATE TRIGGER messages_plain_delete AFTER DELETE ON messages
BEGIN DELETE FROM messages_plain_dirty WHERE message_id=OLD.id; END;
CREATE TRIGGER messages_plain_input AFTER INSERT ON attempt_inputs
BEGIN INSERT OR IGNORE INTO messages_plain_runs VALUES(NEW.attempt_id); END;
CREATE TRIGGER messages_plain_input_moved AFTER UPDATE OF attempt_id ON attempt_inputs
BEGIN INSERT OR IGNORE INTO messages_plain_runs VALUES(NEW.attempt_id); END;
CREATE TRIGGER messages_plain_run AFTER UPDATE OF task_id, job_id ON attempts
BEGIN INSERT OR IGNORE INTO messages_plain_runs VALUES(NEW.id); END;
CREATE TRIGGER messages_plain_run_event AFTER INSERT ON events
WHEN NEW.action IN ('task.next-run.carried', 'attempt.input.moved') AND NEW.target IS NOT NULL
BEGIN INSERT OR IGNORE INTO messages_plain_runs VALUES(NEW.target); END;
-- A room bound to a meeting or another task: its messages, the runs that read them and every reply below.
CREATE TABLE messages_plain_rooms(conversation_id TEXT PRIMARY KEY) WITHOUT ROWID;
CREATE TRIGGER messages_plain_room AFTER UPDATE OF task_id, room_key, audience ON conversations
WHEN OLD.task_id IS NOT NEW.task_id OR OLD.room_key IS NOT NEW.room_key OR OLD.audience IS NOT NEW.audience
BEGIN INSERT OR IGNORE INTO messages_plain_rooms VALUES(NEW.id); END;
-- Anything the read index (backend/read_deps.py) found a settled message or run depends on changed: check it again.
CREATE TRIGGER messages_plain_read_dirty AFTER INSERT ON read_dirty
WHEN NEW.key LIKE 'm:%' OR NEW.key LIKE 'a:%'
BEGIN
INSERT OR IGNORE INTO messages_plain_dirty SELECT substr(NEW.key, 3) WHERE NEW.key LIKE 'm:%';
INSERT OR IGNORE INTO messages_plain_runs SELECT substr(NEW.key, 3) WHERE NEW.key LIKE 'a:%';
END;
