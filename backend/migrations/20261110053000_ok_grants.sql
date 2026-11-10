-- An OK bound to exactly what was shown, used once (backend/task_asks.py, docs/dev/pipelines-v3-cutover.md
-- "Approvals and questions"). One grant per OK requirement: who asked, what will be done (`subject`), the hash and the
-- canonical JSON of the content asked about, who OK'd it and with which answer, and when it was used and by whom.
-- `filed` says ask_ok filed the task for this OK: it ends with the ask. No foreign key: a deleted task takes its
-- requirements with it, and a grant whose requirement is gone is used by nobody.
CREATE TABLE IF NOT EXISTS ok_grants (
  requirement_id TEXT PRIMARY KEY,
  asked_by_id TEXT NOT NULL,
  subject TEXT NOT NULL,
  content_hash TEXT NOT NULL,
  content TEXT NOT NULL DEFAULT 'null' CHECK (json_valid(content)),
  filed INTEGER NOT NULL DEFAULT 0 CHECK (filed IN (0, 1)),
  created_at TEXT NOT NULL,
  ok_at TEXT,
  ok_by_id TEXT,
  answer_id TEXT,
  used_at TEXT,
  used_by_id TEXT,
  CHECK ((ok_at IS NULL) = (ok_by_id IS NULL)),
  CHECK ((used_at IS NULL) = (used_by_id IS NULL)),
  CHECK (used_at IS NULL OR ok_at IS NOT NULL)
);
CREATE INDEX IF NOT EXISTS ok_grants_subject ON ok_grants(subject, created_at);

-- Who was shown which content of an OK, in their own session: give_ok passes only for content its person was shown.
CREATE TABLE IF NOT EXISTS ok_shown (
  requirement_id TEXT NOT NULL,
  person_id TEXT NOT NULL,
  content_hash TEXT NOT NULL,
  shown_at TEXT NOT NULL,
  PRIMARY KEY (requirement_id, person_id, content_hash)
);
