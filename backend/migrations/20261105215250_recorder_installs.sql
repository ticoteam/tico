-- The Tico Recorder each person last used, one row per copy (backend/recorder_installs.py).
CREATE TABLE IF NOT EXISTS recorder_installs (
 person_id TEXT NOT NULL REFERENCES humans(id) ON DELETE CASCADE,
 install_id TEXT NOT NULL,
 version TEXT NOT NULL,
 last_seen_at TEXT NOT NULL,
 created_at TEXT NOT NULL,
 updated_at TEXT NOT NULL,
 PRIMARY KEY(person_id, install_id)
);
