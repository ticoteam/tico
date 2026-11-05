"""Keep fresh replacement codes distinct from the old machine's enrollment codes."""

from backend import hubdb as H


def up(conn):
    # The module schema creates the new shape after migrations on a fresh database.
    if H._has_table(conn, "cloud_machine_codes") and "replacement_for" not in {
            row[1] for row in conn.execute("PRAGMA table_info(cloud_machine_codes)")}:
        conn.execute("ALTER TABLE cloud_machine_codes ADD COLUMN replacement_for TEXT")
