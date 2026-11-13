"""Where one task's runs compact (`task_run_models.compact_at`, backend/task_run_models.py): one of the twelve sizes,
or NULL for the bot's own window. A new file has no table yet: hubdb's CREATE carries the column."""


def up(conn):
    columns = {row[1] for row in conn.execute("PRAGMA table_info(task_run_models)")}
    if columns and "compact_at" not in columns:
        conn.execute("ALTER TABLE task_run_models ADD COLUMN compact_at INTEGER")
