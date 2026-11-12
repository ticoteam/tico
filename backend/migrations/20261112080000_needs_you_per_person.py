"""One person's Needs you reads only the tasks that can reach them (waiting_on.candidates), by index.

- `tasks_open_pipeline`: the open tasks on the engine, with the columns a person's ladder check reads, so the read
  of them never visits a closed task or a task body. The predicate is waiting_on._OPEN_SQL's, word for word.
- `visits_open_person`: the open visits the ladder already brought to a person.
- `answers_give`: the OKs and pages given to someone (`give`), by whom they were given to.
"""

INDEXES = (
    "CREATE INDEX IF NOT EXISTS tasks_open_pipeline ON tasks("
    "id, owner_id, requester_id, private, readers, pipeline_id, stage_id) "
    "WHERE pipeline_id IS NOT NULL AND status='open'",
    "CREATE INDEX IF NOT EXISTS visits_open_person ON visits(person_id) WHERE left_at IS NULL AND person_id IS NOT NULL",
    "CREATE INDEX IF NOT EXISTS answers_give ON answers(notes) WHERE answer='give'",
)


def up(conn):
    tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    if not {"tasks", "visits", "answers"} <= tables:
        return
    for statement in INDEXES:
        conn.execute(statement)
