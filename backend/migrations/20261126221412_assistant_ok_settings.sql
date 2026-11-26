-- Whether a person lets their own Assistant give OKs for them on their word (backend/assistant_oks.py). A person with
-- no row has it off; only their own click in Tico writes their row.
CREATE TABLE assistant_ok_settings (
  person_id TEXT PRIMARY KEY,
  gives_oks INTEGER NOT NULL CHECK (gives_oks IN (0, 1)),
  updated_at TEXT NOT NULL
);
