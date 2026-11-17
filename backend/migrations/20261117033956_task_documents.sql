-- A task's documents (backend/task_documents.py): one file per fixed name per task (Spec, QA report, Release report,
-- Invoice, Email). The file is the task's own versioned attachment (bot_files), so its versions, writers and notes are
-- the ones every task file keeps; this row only names it.
CREATE TABLE IF NOT EXISTS task_documents (
  id TEXT PRIMARY KEY,
  task_id TEXT NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
  name TEXT NOT NULL,
  file_id TEXT NOT NULL REFERENCES bot_files(id),
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  UNIQUE(task_id, name)
);
CREATE INDEX IF NOT EXISTS task_documents_file_id ON task_documents(file_id);
