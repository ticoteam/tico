"""SQLite migration for durable meeting sources."""
MEETING_SCHEMA = """
CREATE TABLE IF NOT EXISTS meetings (
 id TEXT PRIMARY KEY, title TEXT NOT NULL, owner TEXT NOT NULL,
 recorded_by TEXT, uploaded_by TEXT, metadata_json TEXT NOT NULL,
 transcript_original TEXT NOT NULL DEFAULT '', transcript_readable TEXT NOT NULL DEFAULT '',
 notes TEXT NOT NULL DEFAULT '', content_hash TEXT NOT NULL DEFAULT '',
 created TEXT NOT NULL, updated TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS meeting_versions (
 id INTEGER PRIMARY KEY AUTOINCREMENT, meeting_id TEXT NOT NULL REFERENCES meetings(id) ON DELETE CASCADE,
 content_hash TEXT NOT NULL, metadata_json TEXT NOT NULL, transcript_original TEXT NOT NULL,
 transcript_readable TEXT NOT NULL, notes TEXT NOT NULL, created TEXT NOT NULL,
 UNIQUE(meeting_id, content_hash)
);
CREATE TABLE IF NOT EXISTS meeting_deliveries (
 meeting_id TEXT PRIMARY KEY REFERENCES meetings(id) ON DELETE CASCADE,
 requested_by TEXT NOT NULL, sender_name TEXT NOT NULL, destination TEXT NOT NULL,
 instructions TEXT NOT NULL, attachments_json TEXT NOT NULL DEFAULT '[]',
 status TEXT NOT NULL DEFAULT 'pending', task_id TEXT, error TEXT, attempts INTEGER NOT NULL DEFAULT 0,
 requested_at TEXT NOT NULL, delivered_at TEXT
);
CREATE TABLE IF NOT EXISTS task_delegations (
 task_id TEXT NOT NULL REFERENCES tasks(id), delegate TEXT NOT NULL, requested_by TEXT NOT NULL,
 message_id TEXT PRIMARY KEY REFERENCES messages(id), expires TEXT NOT NULL
);
"""

MEETING_REVIEW_SCHEMA = """
ALTER TABLE meetings ADD COLUMN review_state TEXT NOT NULL DEFAULT 'live'
 CHECK (review_state IN ('pending','live','dismissed'));
UPDATE meetings SET metadata_json=json_set(metadata_json,'$.review_state','live','$.ready_announced',json('true'))
 WHERE review_state='live';
CREATE INDEX IF NOT EXISTS meetings_review_owner ON meetings(review_state,owner COLLATE NOCASE);
"""

# The three sections a meeting fills up, plus the two read-only context lists
# Items live beside
# the meeting rather than inside its notes so they can be edited and pushed for as long as the
# meeting exists. `decision` and `question` rows are context and are never pushed.
MEETING_ITEMS_SCHEMA = """
CREATE TABLE IF NOT EXISTS meeting_items (
 id TEXT PRIMARY KEY,
 meeting_id TEXT NOT NULL REFERENCES meetings(id) ON DELETE CASCADE,
 section TEXT NOT NULL CHECK (section IN ('doc','task','feature','decision','question')),  -- the last two: legacy rows, never listed
 text TEXT NOT NULL,
 detail_json TEXT NOT NULL DEFAULT '{}',
 quote TEXT NOT NULL DEFAULT '',
 at_ms INTEGER,
 status TEXT NOT NULL DEFAULT 'proposed' CHECK (status IN ('proposed','pushed','dismissed')),
 created_by TEXT NOT NULL,
 updated_by TEXT,
 pushed_at TEXT,
 result_ref TEXT,
 created TEXT NOT NULL,
 updated TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS meeting_items_meeting ON meeting_items(meeting_id, section, created);
"""

# What the brain has already thought about one meeting (`backend/meeting_brain.py`). It lives in a
# table rather than in memory so the cadence survives a restart and `hub sql` can answer "is the
# brain keeping up?". `last_turn_index` is how far into `meta.turns` the last think read;
# `final_at` is the one think that runs right after Stop; `error_at` backs a failing meeting off.
MEETING_BRAIN_SCHEMA = """
CREATE TABLE IF NOT EXISTS meeting_brain (
 meeting_id TEXT PRIMARY KEY REFERENCES meetings(id) ON DELETE CASCADE,
 last_think_at TEXT,
 last_turn_index INTEGER NOT NULL DEFAULT 0,
 runs INTEGER NOT NULL DEFAULT 0,
 last_error TEXT,
 error_at TEXT,
 final_at TEXT,
 updated TEXT NOT NULL
);
"""

# What people say about a meeting, beside it. Anyone who can open the meeting can read the thread
# and add to it (the recorder's own `note` on the record stays theirs); a comment is never edited,
# only added, so the thread reads as it happened. Visible exactly where its meeting is.
MEETING_COMMENTS_SCHEMA = """
CREATE TABLE IF NOT EXISTS meeting_comments (
 id TEXT PRIMARY KEY,
 meeting_id TEXT NOT NULL REFERENCES meetings(id) ON DELETE CASCADE,
 author TEXT NOT NULL,
 text TEXT NOT NULL,
 at_ms INTEGER,
 created TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS meeting_comments_meeting ON meeting_comments(meeting_id, created);
"""

# An outside activity may point at a recording already captured in Tico. Keep each transcript
# revision independently: a provider correction must not silently rewrite a person's sent work.
RECORDING_SOURCES_SCHEMA = """
CREATE TABLE IF NOT EXISTS recording_source_refs (
 source TEXT NOT NULL, resource_type TEXT NOT NULL, external_id TEXT NOT NULL,
 meeting_id TEXT NOT NULL REFERENCES meetings(id) ON DELETE CASCADE,
 created TEXT NOT NULL,
 PRIMARY KEY(source,resource_type,external_id)
);
CREATE INDEX IF NOT EXISTS recording_source_meeting ON recording_source_refs(meeting_id);
CREATE TABLE IF NOT EXISTS recording_transcripts (
 id INTEGER PRIMARY KEY AUTOINCREMENT,
 meeting_id TEXT NOT NULL REFERENCES meetings(id) ON DELETE CASCADE,
 source TEXT NOT NULL, resource_type TEXT NOT NULL, external_id TEXT NOT NULL,
 transcript_index INTEGER NOT NULL DEFAULT 0,
 content_hash TEXT NOT NULL, source_updated_at TEXT NOT NULL,
 turns_json TEXT NOT NULL, transcript_text TEXT NOT NULL, summary_text TEXT NOT NULL DEFAULT '',
 imported_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS recording_transcripts_meeting ON recording_transcripts(meeting_id,id DESC);
"""

# Live, text-only meetings have their own append-only sequence and replay cursor. Their bot
# membership is deliberately separate from bot_config.access_json: joining one meeting never
# changes a bot's standing company permissions.
LIVE_MEETINGS_SCHEMA = """
CREATE TABLE IF NOT EXISTS live_meetings (
 id TEXT PRIMARY KEY,
 owner_actor TEXT NOT NULL,
 title TEXT NOT NULL,
 state TEXT NOT NULL CHECK (state IN ('live','paused','ended')),
 client_id TEXT,
 seq INTEGER NOT NULL DEFAULT 0,
 next_event_id INTEGER NOT NULL DEFAULT 0,
 window_ms INTEGER NOT NULL CHECK (window_ms BETWEEN 15000 AND 45000),
 cooldown_ms INTEGER NOT NULL DEFAULT 60000,
 threshold REAL NOT NULL DEFAULT 0.7,
 imported_meeting_id TEXT,
 created TEXT NOT NULL,
 updated TEXT NOT NULL,
 started_at TEXT NOT NULL,
 ended_at TEXT,
 UNIQUE(owner_actor,client_id)
);
CREATE INDEX IF NOT EXISTS live_meetings_state ON live_meetings(state,updated DESC);
CREATE TABLE IF NOT EXISTS live_meeting_humans (
 meeting_id TEXT NOT NULL REFERENCES live_meetings(id) ON DELETE CASCADE,
 actor TEXT NOT NULL,
 joined_at TEXT NOT NULL,
 PRIMARY KEY(meeting_id,actor)
);
CREATE TABLE IF NOT EXISTS live_meeting_bots (
 meeting_id TEXT NOT NULL REFERENCES live_meetings(id) ON DELETE CASCADE,
 bot TEXT NOT NULL,
 joined_by TEXT NOT NULL,
 joined_at TEXT NOT NULL,
 last_reply_ms INTEGER,
 PRIMARY KEY(meeting_id,bot)
);
CREATE TABLE IF NOT EXISTS live_meeting_chunks (
 meeting_id TEXT NOT NULL REFERENCES live_meetings(id) ON DELETE CASCADE,
 seq INTEGER NOT NULL,
 revision INTEGER NOT NULL DEFAULT 1,
 speaker TEXT NOT NULL,
 start_ms INTEGER NOT NULL,
 end_ms INTEGER NOT NULL,
 text TEXT NOT NULL,
 created TEXT NOT NULL,
 PRIMARY KEY(meeting_id,seq)
);
CREATE INDEX IF NOT EXISTS live_meeting_chunks_time ON live_meeting_chunks(meeting_id,start_ms,seq);
CREATE TABLE IF NOT EXISTS live_meeting_chunk_versions (
 meeting_id TEXT NOT NULL REFERENCES live_meetings(id) ON DELETE CASCADE,
 seq INTEGER NOT NULL,
 revision INTEGER NOT NULL,
 speaker TEXT NOT NULL,
 start_ms INTEGER NOT NULL,
 end_ms INTEGER NOT NULL,
 text TEXT NOT NULL,
 authored_by TEXT NOT NULL,
 created TEXT NOT NULL,
 PRIMARY KEY(meeting_id,seq,revision)
);
CREATE TABLE IF NOT EXISTS live_meeting_chat (
 id TEXT PRIMARY KEY,
 meeting_id TEXT NOT NULL REFERENCES live_meetings(id) ON DELETE CASCADE,
 actor TEXT NOT NULL,
 role TEXT NOT NULL CHECK (role IN ('human','bot')),
 text TEXT NOT NULL,
 at_ms INTEGER,
 transcript_seq INTEGER,
 turn_id TEXT,
 created TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS live_meeting_chat_order ON live_meeting_chat(meeting_id,created,id);
CREATE UNIQUE INDEX IF NOT EXISTS live_meeting_chat_turn ON live_meeting_chat(turn_id) WHERE turn_id IS NOT NULL;
CREATE TABLE IF NOT EXISTS live_meeting_events (
 meeting_id TEXT NOT NULL REFERENCES live_meetings(id) ON DELETE CASCADE,
 event_id INTEGER NOT NULL,
 type TEXT NOT NULL,
 payload_json TEXT NOT NULL,
 created TEXT NOT NULL,
 PRIMARY KEY(meeting_id,event_id)
);
CREATE TABLE IF NOT EXISTS live_meeting_windows (
 meeting_id TEXT NOT NULL REFERENCES live_meetings(id) ON DELETE CASCADE,
 window_index INTEGER NOT NULL,
 start_ms INTEGER NOT NULL,
 end_ms INTEGER NOT NULL,
 status TEXT NOT NULL CHECK (status IN ('deciding','complete','interrupted')),
 outcome TEXT NOT NULL DEFAULT '',
 trace_json TEXT NOT NULL DEFAULT '{}',
 started TEXT NOT NULL,
 finished TEXT,
 PRIMARY KEY(meeting_id,window_index)
);
CREATE TABLE IF NOT EXISTS live_meeting_turns (
 id TEXT PRIMARY KEY,
 meeting_id TEXT NOT NULL REFERENCES live_meetings(id) ON DELETE CASCADE,
 bot TEXT NOT NULL,
 window_index INTEGER,
 source_key TEXT NOT NULL,
 transcript_seq INTEGER,
 status TEXT NOT NULL CHECK (status IN ('pending','claimed','replied','skipped')),
 skip_reason TEXT NOT NULL DEFAULT '',
 created TEXT NOT NULL,
 claimed_at TEXT,
 replied_at TEXT,
 UNIQUE(meeting_id,source_key,bot)
);
CREATE INDEX IF NOT EXISTS live_meeting_turns_pending ON live_meeting_turns(meeting_id,bot,status,created);
"""

# Per-meeting routing limits are configurable from the connect request. Keep this separate from
# LIVE_MEETINGS_SCHEMA: that migration has shipped, and migration indexes are append-only.
LIVE_MEETINGS_ROUTING_LIMITS_SCHEMA = "ALTER TABLE live_meetings ADD COLUMN reply_cap INTEGER NOT NULL DEFAULT 3 CHECK (reply_cap BETWEEN 1 AND 20);"
