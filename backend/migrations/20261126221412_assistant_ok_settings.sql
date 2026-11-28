-- Whether a person lets their own Assistant give OKs for them on their word (backend/assistant_oks.py). A person with
-- no row has it off; only their own click in Tico writes their row.
CREATE TABLE assistant_ok_settings (
  person_id TEXT PRIMARY KEY,
  gives_oks INTEGER NOT NULL CHECK (gives_oks IN (0, 1)),
  updated_at TEXT NOT NULL
);

-- The person's words that lent an OK through their Assistant: one message, one OK, never lent again, even after an
-- Undo (backend/assistant_oks.py `use`, written in the answer's transaction).
CREATE TABLE assistant_ok_uses (
  message_id TEXT PRIMARY KEY,
  answer_id TEXT NOT NULL,
  task_id TEXT NOT NULL,
  visit_id TEXT NOT NULL,
  requirement_id TEXT NOT NULL,
  created_at TEXT NOT NULL
);
