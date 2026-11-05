"""Count each failed enrollment id once, before its deletion reply or a replacement can arrive."""

from backend import hubdb as H


def up(conn):
    conn.execute("CREATE TABLE IF NOT EXISTS cloud_machine_enrollment_failures("
                 "machine_id TEXT PRIMARY KEY, bot TEXT NOT NULL)")
    if not H._has_table(conn, "cloud_machines"):
        return
    if "create_asked_at" not in {row[1] for row in conn.execute("PRAGMA table_info(cloud_machines)")}:
        conn.execute("ALTER TABLE cloud_machines ADD COLUMN create_asked_at REAL")
    # Dead rows were already counted by the old manager; seed their ids without spending another try.
    conn.execute("INSERT OR IGNORE INTO cloud_machine_enrollment_failures(machine_id,bot) "
                 "SELECT machine_id,bot FROM cloud_machines WHERE machine_id IS NOT NULL AND state IN ('failed','deleted')")
    if not H._has_table(conn, "cloud_machine_deletions"):
        return
    # A lost DELETE reply could leave an uncounted intent, even after its bot adopted another machine.
    for machine_id, bot in conn.execute("SELECT machine_id,bot FROM cloud_machine_deletions "
                                        "WHERE reason='never_enrolled'").fetchall():
        added = conn.execute("INSERT OR IGNORE INTO cloud_machine_enrollment_failures(machine_id,bot) VALUES(?,?)",
                             (machine_id, bot)).rowcount
        if added:
            conn.execute("UPDATE cloud_machines SET enroll_fails=enroll_fails+1 WHERE bot=?", (bot,))
