"""A pipeline's manager seat, and when a spawn link files (org v2).

- task_types.manager_seat: the open seat whose hire makes the pipeline's manager (pipeline_setup.seat_filled). Set by the
  setup's seat binding; cleared by nothing, so the seat stays on record once it is filled.
- pipeline_links.fires: 'leave' (the spawn files as its card leaves the stage, every spawn so far) or 'enter' (it files as a
  card enters the stage: Lead's Qualified files one Opportunity). The default keeps every existing link as it was.

Both columns are added only when their table exists and the column is missing, so a fresh database and an upgraded one
end up the same. On a fresh file the numbered hubdb steps (SCHEMA, PIPELINES_SCHEMA) create task_types before the
migration files run, so the column lands here on every file and nothing else adds it.
"""


def up(conn):
    tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    if "task_types" in tables:
        columns = {row[1] for row in conn.execute("PRAGMA table_info(task_types)")}
        if "manager_seat" not in columns:
            conn.execute("ALTER TABLE task_types ADD COLUMN manager_seat TEXT")
    if "pipeline_links" in tables:
        columns = {row[1] for row in conn.execute("PRAGMA table_info(pipeline_links)")}
        if "fires" not in columns:
            conn.execute(
                "ALTER TABLE pipeline_links ADD COLUMN fires TEXT NOT NULL DEFAULT 'leave' "
                "CHECK (fires IN ('leave', 'enter'))"
            )
