CREATE TABLE IF NOT EXISTS attempt_reads (
 attempt_id TEXT NOT NULL REFERENCES attempts(id) ON DELETE CASCADE,
 kind TEXT NOT NULL CHECK(kind IN ('meeting','file')),
 ref TEXT NOT NULL,
 created_at TEXT NOT NULL,
 PRIMARY KEY(attempt_id,kind,ref)
);
