"""Whether an inbox's mail feeds the Directory (backend/entities/ingest.py). On by default; its owner may turn it off, and a
bot's inbox always feeds it whatever this says (backend/mailbox_access.py feeds_directory)."""


def up(conn):
    columns = {row[1] for row in conn.execute("PRAGMA table_info(mailboxes)")}
    if columns and "feeds_directory" not in columns:
        conn.execute(
            "ALTER TABLE mailboxes ADD COLUMN feeds_directory INTEGER NOT NULL DEFAULT 1 CHECK (feeds_directory IN (0, 1))"
        )
