"""An existing cloud_machines table gains `move_drain` (backend/cloud_machines.py `moving`): when a bot moving to its
cloud machine's computer began taking no new turns on the computer it is leaving, waiting for its running turns to end;
NULL for none.

cloud_machines is created by the module schema code after the migration files run (hubdb.migrate), and that CREATE has
the column, so a new database has no table here yet and the step is skipped. Running it again changes nothing.
"""


def up(conn):
    if not conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='cloud_machines'").fetchone():
        return
    if "move_drain" not in {row[1] for row in conn.execute("PRAGMA table_info(cloud_machines)")}:
        conn.execute("ALTER TABLE cloud_machines ADD COLUMN move_drain REAL")
