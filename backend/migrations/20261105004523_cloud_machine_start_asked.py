"""An existing cloud_machines table gains start_asked_at: when the hub last asked HQ to start the machine and had no
answer yet (backend/cloud_machines.py). A running machine in HQ's list then counts as a lost start's resume only when
that marker is set, so a stop that has not landed yet is never read as one. NULL is no start pending.

cloud_machines is created by the module schema code after the migration files run (hubdb.migrate), and that CREATE has
the column, so a new database has no table here yet and the step is skipped. Running it again changes nothing.
"""

from backend import hubdb as H


def up(conn):
    if not H._has_table(conn, "cloud_machines"):
        return
    columns = {row[1] for row in conn.execute("PRAGMA table_info(cloud_machines)")}
    if "start_asked_at" not in columns:
        conn.execute("ALTER TABLE cloud_machines ADD COLUMN start_asked_at REAL")
