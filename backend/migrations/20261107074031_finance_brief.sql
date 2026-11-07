-- Finance briefing (backend/finance/brief.py): one row per reader, the lines they were last handed and a fingerprint
-- of them. Same fingerprint keeps `as_of`; a change moves `as_of` to now and keeps the old one as `changed_since`.
-- `delivered_fingerprint` is the fingerprint handed at the reader's last turn start (for_turn): lines go out only when it differs.
-- A cache of reads that already cut to the reader: it holds no figure a reader could not read, and a dev copy may empty it.
CREATE TABLE IF NOT EXISTS fin_briefs (
 reader TEXT PRIMARY KEY,
 fingerprint TEXT NOT NULL,
 lines TEXT NOT NULL DEFAULT '[]',
 as_of TEXT NOT NULL,
 changed_since TEXT,
 view_version INTEGER NOT NULL DEFAULT 0,
 created_at TEXT NOT NULL,
 delivered_fingerprint TEXT NOT NULL DEFAULT ''
);
