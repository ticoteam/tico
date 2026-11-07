-- Feedback on bot work (backend/feedback.py): one person's thumbs up or down, with an optional reason, on a message, task
-- or approval a bot made. No copy of the rated words: deleting a message erases them, and the reason is the person's own.
CREATE TABLE IF NOT EXISTS feedbacks(id TEXT PRIMARY KEY, person_id TEXT NOT NULL,
 subject_type TEXT NOT NULL CHECK (subject_type IN ('message','task','approval')), subject_id TEXT NOT NULL,
 bot_id TEXT NOT NULL, conversation_id TEXT, attempt_id TEXT, model TEXT, harness TEXT, subject_revision INTEGER,
 rating TEXT NOT NULL CHECK (rating IN ('up','down')),
 reason TEXT NOT NULL DEFAULT '' CHECK (length(reason) <= 1000),
 outcome TEXT NOT NULL DEFAULT 'none' CHECK (outcome IN ('none','told_bot','rule')), note_id TEXT, org_rule_id TEXT,
 created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
CREATE UNIQUE INDEX IF NOT EXISTS feedbacks_person_subject ON feedbacks(person_id, subject_type, subject_id);
CREATE INDEX IF NOT EXISTS feedbacks_bot_created ON feedbacks(bot_id, created_at);
CREATE INDEX IF NOT EXISTS feedbacks_subject ON feedbacks(subject_type, subject_id);
CREATE INDEX IF NOT EXISTS feedbacks_person_updated ON feedbacks(person_id, updated_at);
