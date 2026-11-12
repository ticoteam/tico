"""A run's prompt-cache writes on their own: `cache_write_tokens` on `attempts` and `usage_facts`.

A runner that reports them (Claude's `cache_creation_input_tokens`) no longer counts them in `input_tokens`, which is then
the uncached input only. A runtime with no cache writes (Codex, Gemini, Cursor, pi) records 0. Rows from before this
column, or from a runner too old to report it, are NULL: their `input_tokens` still include any cache writes. No
backfill: an old row cannot be split.
"""


def up(conn):
    for table in ("attempts", "usage_facts"):
        columns = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
        if columns and "cache_write_tokens" not in columns:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN cache_write_tokens INTEGER")
