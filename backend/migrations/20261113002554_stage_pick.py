"""A stage picks its owner from several bots (backend/stage_pick.py, "One of several").

- `stages.pick`: the stage's pick, as JSON, when its owner is "One of several" (owner `pick`): the bots, Take turns or
  Random, Limit each, Prefer, and whether the pick becomes the task's owner. NULL for every other stage.
- `visits.holder_id`: the bot a pick gave this visit (a Reviewer holds only the review). A task back in the stage goes to
  the same one.
- `visits.pick_state` and `visits.pick_why`: a task nobody was picked for: `full` (it waits for a free one, nobody is
  woken) or `nobody` (the manager is woken, told why).
- `stage_pick_turns`: per stage, the bot Take turns gave the last task.
"""


def _columns(conn, table):
    return {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}


def up(conn):
    stages = _columns(conn, "stages")
    if stages and "pick" not in stages:
        conn.execute("ALTER TABLE stages ADD COLUMN pick TEXT CHECK (pick IS NULL OR json_valid(pick))")
    visits = _columns(conn, "visits")
    if visits:
        if "holder_id" not in visits:
            conn.execute("ALTER TABLE visits ADD COLUMN holder_id TEXT")
        if "pick_state" not in visits:
            conn.execute(
                "ALTER TABLE visits ADD COLUMN pick_state TEXT CHECK (pick_state IS NULL OR pick_state IN "
                "('full', 'nobody'))"
            )
        if "pick_why" not in visits:
            conn.execute("ALTER TABLE visits ADD COLUMN pick_why TEXT")
        conn.execute(
            "CREATE INDEX IF NOT EXISTS visits_holder ON visits(task_id, stage_id) WHERE holder_id IS NOT NULL"
        )
    conn.execute(
        "CREATE TABLE IF NOT EXISTS stage_pick_turns ("
        "stage_id TEXT PRIMARY KEY, bot_id TEXT NOT NULL, at TEXT NOT NULL)"
    )
