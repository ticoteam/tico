"""A salted OS device identity proves that two enrollments belong to the same computer.

Existing computers stay unknown until their runner reports one; labels never backfill an identity.
"""


def up(conn):
    columns = {row[1] for row in conn.execute("PRAGMA table_info(runners)")}
    if not columns:
        return
    if "device_id" not in columns:
        conn.execute("ALTER TABLE runners ADD COLUMN device_id TEXT")
    conn.execute("CREATE INDEX IF NOT EXISTS runners_device_id ON runners(device_id,owner_id,created_at) "
                 "WHERE device_id IS NOT NULL AND revoked_at IS NULL")
