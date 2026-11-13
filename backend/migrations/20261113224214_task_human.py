"""A task's Human (`tasks.human_id`): the one person accountable for a task whose owner may be a bot, `human:<id>` or
NULL. The task's person (waiting_on.person_for) is its Human first, so asks that name nobody reach them. Existing tasks
have none."""


def up(conn):
    have = {r[1] for r in conn.execute("PRAGMA table_info(tasks)")}
    if "human_id" not in have:
        conn.execute("ALTER TABLE tasks ADD COLUMN human_id TEXT")
    # One person's Needs you reads the open tasks they are the Human of (waiting_on.candidates): few, by index.
    conn.execute("CREATE INDEX IF NOT EXISTS tasks_human ON tasks(human_id) WHERE human_id IS NOT NULL")
