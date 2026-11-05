"""Keep the age of each deletion fence after its bot moves to a fresh machine."""

import time

from backend import hubdb as H


def up(conn):
    conn.execute("CREATE TABLE IF NOT EXISTS cloud_machine_deletions("
                 "machine_id TEXT PRIMARY KEY, bot TEXT NOT NULL, runner_id TEXT, "
                 "reason TEXT NOT NULL DEFAULT 'manual', next_try_at REAL, failures INTEGER NOT NULL DEFAULT 0, "
                 "created_at REAL)")
    if "created_at" not in {row[1] for row in conn.execute("PRAGMA table_info(cloud_machine_deletions)")}:
        conn.execute("ALTER TABLE cloud_machine_deletions ADD COLUMN created_at REAL")
    if H._has_table(conn, "events"):
        # A completed local deletion records the old id even after the bot row has moved on.
        conn.execute("UPDATE cloud_machine_deletions SET created_at=("
                     "SELECT min(CAST(strftime('%s',e.created_at) AS REAL)) FROM events e "
                     "WHERE e.action='cloud_machine.deleted' AND json_valid(e.detail) "
                     "AND json_extract(e.detail,'$.machine')=cloud_machine_deletions.machine_id) "
                     "WHERE created_at IS NULL")
    # An unanswered request may leave no dated receipt; start its age here rather than inventing one.
    conn.execute("UPDATE cloud_machine_deletions SET created_at=? WHERE created_at IS NULL", (time.time(),))
