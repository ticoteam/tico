"""The Heartbeat (backend/heartbeat.py, docs/heartbeat.md): one short beat runs the steps that must happen within seconds.
What each beat did is kept small:
  - heartbeat_steps: one row per step, rewritten in the beat's one transaction: its last run, time and result, the
    last time it did something and what, its last error, and day counts kept in two 12-hour windows (`window_*`);
  - heartbeat_beats: one row only for a beat where a step did something or failed, with those steps' lines (JSON);
    pruned past 7 days or a row cap, hourly, inside the beat;
  - heartbeat_states: one row (id 1): the last beat, the beats in the day windows, quiet beats counted, not stored.

Plain SQL; running it again changes nothing.
"""


def up(conn):
    conn.execute("""CREATE TABLE IF NOT EXISTS heartbeat_steps(
 key TEXT PRIMARY KEY,
 last_run_at TEXT,
 last_ms INTEGER,
 last_result TEXT,
 last_did_at TEXT,
 last_did TEXT,
 last_error TEXT,
 last_error_at TEXT,
 window_started_at TEXT,
 runs_count INTEGER NOT NULL DEFAULT 0,
 did_count INTEGER NOT NULL DEFAULT 0,
 failed_count INTEGER NOT NULL DEFAULT 0,
 previous_runs_count INTEGER NOT NULL DEFAULT 0,
 previous_did_count INTEGER NOT NULL DEFAULT 0,
 previous_failed_count INTEGER NOT NULL DEFAULT 0,
 created_at TEXT NOT NULL,
 updated_at TEXT NOT NULL)""")
    conn.execute("""CREATE TABLE IF NOT EXISTS heartbeat_beats(
 id TEXT PRIMARY KEY,
 started_at TEXT NOT NULL,
 total_ms INTEGER NOT NULL DEFAULT 0,
 did_count INTEGER NOT NULL DEFAULT 0,
 failed_count INTEGER NOT NULL DEFAULT 0,
 steps TEXT NOT NULL DEFAULT '[]',
 created_at TEXT NOT NULL)""")
    conn.execute("CREATE INDEX IF NOT EXISTS heartbeat_beats_started_at ON heartbeat_beats(started_at, id)")
    conn.execute("""CREATE TABLE IF NOT EXISTS heartbeat_states(
 id INTEGER PRIMARY KEY CHECK (id = 1),
 last_beat_at TEXT,
 window_started_at TEXT,
 beats_count INTEGER NOT NULL DEFAULT 0,
 previous_beats_count INTEGER NOT NULL DEFAULT 0,
 quiet_beats_count INTEGER NOT NULL DEFAULT 0,
 quiet_since_at TEXT,
 last_pruned_at TEXT,
 created_at TEXT NOT NULL,
 updated_at TEXT NOT NULL)""")
