"""The one row behind "Sleep when idle" (backend/sleep_idle.py); hubdb.MIGRATIONS applies it."""
SCHEMA = """
CREATE TABLE IF NOT EXISTS sleep_idle_state (
  singleton INTEGER PRIMARY KEY CHECK(singleton=1), hours INTEGER NOT NULL DEFAULT 0,
  runners INTEGER NOT NULL DEFAULT 1, changed_by TEXT NOT NULL DEFAULT '', changed_at TEXT,
  hosted INTEGER, layout TEXT NOT NULL DEFAULT '', checked_at REAL,
  last_request_at REAL, last_result TEXT NOT NULL DEFAULT '', last_wake_at TEXT,
  last_error TEXT NOT NULL DEFAULT '', failures INTEGER NOT NULL DEFAULT 0, next_ask REAL);
INSERT OR IGNORE INTO sleep_idle_state(singleton) VALUES(1);
"""
