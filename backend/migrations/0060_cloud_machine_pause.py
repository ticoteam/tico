"""A cloud machine's Pause after (`cloud_machines.pause_minutes`, backend/cloud_machines.py): whole minutes a machine
pauses after with no turn out and no activity, or NULL for the default (PAUSE_MINUTES). On a new file the table does not
exist yet: cloud_machines.SCHEMA makes it with the column."""


def _columns(conn, table):
    return {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}


def up(conn):
    if not conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='cloud_machines'").fetchone():
        return
    if "pause_minutes" not in _columns(conn, "cloud_machines"):
        conn.execute("ALTER TABLE cloud_machines ADD COLUMN pause_minutes INTEGER")
