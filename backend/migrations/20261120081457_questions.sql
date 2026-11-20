-- A question (backend/questions.py): a task a bot files for a person under the task it is working on, while the
-- company switch questions_as_tasks is on. The question task's own row holds its title, description (body), owner (the
-- person), requester (the bot), status, snooze and chat; this row holds only what answering it safely needs. Open while
-- `ended_reason` is null. `round` goes up each time the same bot asks the same person again on the same visit.
-- `source_mark_id` is the ❓ (question_marks) a backfill turned into this question, so running it again adds nothing.
-- No foreign keys: a deleted task goes to the trash with its row kept here, and a restore finds it again.
CREATE TABLE IF NOT EXISTS questions (
  task_id TEXT PRIMARY KEY,
  parent_id TEXT NOT NULL,
  visit_id TEXT NOT NULL,
  asker_id TEXT NOT NULL,
  person_id TEXT NOT NULL,
  round INTEGER NOT NULL DEFAULT 1,
  link_url TEXT,
  source_mark_id TEXT UNIQUE,
  answered_at TEXT,
  answered_by_id TEXT,
  answer_message_id TEXT,
  ended_reason TEXT CHECK (
    ended_reason IN ('answered', 'withdrawn', 'parent_ended', 'parent_moved', 'asker_gone', 'person_gone')
  ),
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS index_questions_on_parent_id_open ON questions(parent_id) WHERE ended_reason IS NULL;
CREATE INDEX IF NOT EXISTS index_questions_on_person_id_open ON questions(person_id) WHERE ended_reason IS NULL;
CREATE INDEX IF NOT EXISTS index_questions_on_visit_id ON questions(visit_id);
