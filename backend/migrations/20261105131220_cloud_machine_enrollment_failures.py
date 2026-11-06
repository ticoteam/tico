"""Count each failed enrollment id once, before its deletion reply or a replacement can arrive."""

from backend import hubdb as H


def up(conn):
    conn.execute("CREATE TABLE IF NOT EXISTS cloud_machine_enrollment_failures("
                 "machine_id TEXT PRIMARY KEY, bot TEXT NOT NULL)")
    if not H._has_table(conn, "cloud_machines"):
        return
    if "create_asked_at" not in {row[1] for row in conn.execute("PRAGMA table_info(cloud_machines)")}:
        conn.execute("ALTER TABLE cloud_machines ADD COLUMN create_asked_at REAL")
    # HQ failures were already counted by the old manager; seed their ids without spending another try.
    conn.execute("INSERT OR IGNORE INTO cloud_machine_enrollment_failures(machine_id,bot) "
                 "SELECT machine_id,bot FROM cloud_machines WHERE machine_id IS NOT NULL AND state='failed'")
    if H._has_table(conn, "events"):
        # An accepted DELETE spent a try and wrote this event, even if its intent outlived several replacements.
        conn.execute("INSERT OR IGNORE INTO cloud_machine_enrollment_failures(machine_id,bot) "
                     "SELECT json_extract(detail,'$.machine'),target FROM events "
                     "WHERE action='cloud_machine.deleted' AND json_valid(detail) "
                     "AND json_extract(detail,'$.reason')='never_enrolled' "
                     "AND typeof(json_extract(detail,'$.machine'))='text'")
    if not H._has_table(conn, "cloud_machine_deletions"):
        return
    # A lost DELETE reply could leave an uncounted intent, even after its bot adopted another machine.
    for machine_id, bot in conn.execute("SELECT machine_id,bot FROM cloud_machine_deletions "
                                        "WHERE reason='never_enrolled'").fetchall():
        added = conn.execute("INSERT OR IGNORE INTO cloud_machine_enrollment_failures(machine_id,bot) VALUES(?,?)",
                             (machine_id, bot)).rowcount
        if added:
            conn.execute("UPDATE cloud_machines SET enroll_fails=enroll_fails+1 WHERE bot=?", (bot,))
