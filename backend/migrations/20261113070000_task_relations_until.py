"""`task_relations.until`: a dependency that holds past its task's end. `released` (the one value, and only on a
`depends_on` row): the task stays blocked after the other one is Done, until production runs a release that holds that
task's merged PRs (backend/task_depends.py). NULL on every other row, so nothing stored changes. A table that has the
column already, or none yet (a new file: task_relations.SCHEMA makes it with the column), is left as it is."""

COLUMN = "until TEXT CHECK (until IS NULL OR (kind = 'depends_on' AND until = 'released'))"


def up(conn):
    columns = {row[1] for row in conn.execute("PRAGMA table_info(task_relations)")}
    # An old Draft #110 table (task_id, related_id) is task_relations.migrate's to replace, at start.
    if "from_task" not in columns or "until" in columns:
        return
    conn.execute(f"ALTER TABLE task_relations ADD COLUMN {COLUMN}")
