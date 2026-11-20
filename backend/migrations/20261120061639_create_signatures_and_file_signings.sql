-- A person's signature (backend/file_signings.py): drawn once in the web app's Sign mode and placed on each PDF they
-- sign there. One per person; drawing it again replaces it. `image` is a PNG data URL.
CREATE TABLE IF NOT EXISTS signatures (
  person_id TEXT PRIMARY KEY REFERENCES humans(id),
  image TEXT NOT NULL,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
-- Each time a person signed a PDF in Tico: the version they signed and the new version their marks made, the SHA-256 of
-- each version's bytes, what they added on which page, and the address and browser the request came from. A row is
-- never changed or removed; the file's own deletion leaves it as the record that the signing happened.
CREATE TABLE IF NOT EXISTS file_signings (
  id TEXT PRIMARY KEY,
  file_id TEXT NOT NULL,
  task_id TEXT,
  signer_id TEXT NOT NULL,
  before_version INTEGER NOT NULL,
  after_version INTEGER NOT NULL,
  before_sha256 TEXT NOT NULL,
  after_sha256 TEXT NOT NULL,
  marks TEXT NOT NULL DEFAULT '[]',
  ip_address TEXT,
  user_agent TEXT,
  created_at TEXT NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS index_file_signings_on_file_id_and_after_version
  ON file_signings(file_id, after_version);
