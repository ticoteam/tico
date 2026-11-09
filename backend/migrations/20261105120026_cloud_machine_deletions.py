"""Keep deletion intent by machine id, independently of a bot's placement and displayed problem."""

from backend import hubdb as H


def up(conn):
    conn.execute("CREATE TABLE IF NOT EXISTS cloud_machine_deletions("
                 "machine_id TEXT PRIMARY KEY, bot TEXT NOT NULL, runner_id TEXT, "
                 "reason TEXT NOT NULL DEFAULT 'manual', next_try_at REAL, failures INTEGER NOT NULL DEFAULT 0)")
    if H._has_table(conn, "cloud_machines"):
        conn.execute("INSERT OR IGNORE INTO cloud_machine_deletions(machine_id,bot,runner_id) "
                     "SELECT machine_id,bot,runner_id FROM cloud_machines "
                     "WHERE machine_id IS NOT NULL AND problem IN ('delete_unknown','replacing')")
