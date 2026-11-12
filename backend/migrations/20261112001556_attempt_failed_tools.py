"""A run's failed tool calls (backend/run_diagnostics.py keep_failed_tools): each one's command and the end of its
output, scrubbed by the runner, as a JSON list read through the run's capture and kept a week, like stderr_tail.

`attempt_diagnostics` is made by the module code (backend/store.py, which has both columns) after the migrations, so a
new file has no table here yet and nothing to add.
"""


def up(conn):
    columns = {row[1] for row in conn.execute("PRAGMA table_info(attempt_diagnostics)")}
    if not columns:
        return
    for column in ("failed_tools", "failed_tools_at"):
        if column not in columns:
            conn.execute(f"ALTER TABLE attempt_diagnostics ADD COLUMN {column} TEXT")
