"""An answer on a page keeps the commits it was given at (docs/pipelines-v3.md, "Fixes 2").

- `answers` gains `heads`: {pr ref: newest commit} of the task's linked PRs when an answer on a page was written
  (item_sources.github.heads, read ones only). A person's OK of a page counts only while those are still the newest
  commits, and a bot's OK of the commits since the reviews names the commits it read. Existing answers keep it empty
  and count as before.
"""


def _columns(conn, table):
    return {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}


def up(conn):
    tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    if "answers" in tables and "heads" not in _columns(conn, "answers"):
        conn.execute("ALTER TABLE answers ADD COLUMN heads TEXT CHECK (heads IS NULL OR json_valid(heads))")
