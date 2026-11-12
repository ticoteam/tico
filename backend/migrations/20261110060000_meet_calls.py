"""Google Meet calls in threads (backend/calls.py, docs/meet.md, docs/dev/meet-calls-contract.md): a call names its
provider, and a Google Meet call keeps its space, what its status source last said (and when it last saw anyone in the
Meet, for the end rule), who was rung, its card and its transcript messages, and whose Recorder records it. A
participant row says whether that person is in the Meet now.

`calls` and `call_participants` come from 0047, so they are there on every file; the guard only keeps a rerun on a
file that already has a column from adding it twice.
"""

CALL_COLUMNS = (
    ("provider", "TEXT NOT NULL DEFAULT 'tico' CHECK (provider IN ('tico','google_meet'))"),
    ("meet_space", "TEXT"),
    ("meet_uri", "TEXT"),
    ("meet_code", "TEXT"),
    ("meet_conference", "TEXT"),
    ("meet_seen_at", "TEXT"),
    ("meet_active_at", "TEXT"),
    ("meet_polled_at", "TEXT"),
    ("live_count", "INTEGER NOT NULL DEFAULT 0"),
    ("status_source", "TEXT CHECK (status_source IN ('google','recorder'))"),
    ("ring", "TEXT"),
    ("card_message_id", "TEXT"),
    ("recorder_actor", "TEXT"),
    ("recorder_claimed_at", "TEXT"),
    ("transcript_message_id", "TEXT"),
    ("meet_empty_at", "TEXT"),  # the first read that showed nobody in a Meet someone had been seen in
)
PARTICIPANT_COLUMNS = (("in_call", "INTEGER NOT NULL DEFAULT 0"),)


def _add(conn, table, wanted):
    have = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
    for name, kind in wanted:
        if name not in have:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {kind}")


def up(conn):
    tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    if "calls" in tables:
        _add(conn, "calls", CALL_COLUMNS)
        conn.execute("CREATE INDEX IF NOT EXISTS calls_live_provider ON calls(state, provider)")
    if "call_participants" in tables:
        _add(conn, "call_participants", PARTICIPANT_COLUMNS)
