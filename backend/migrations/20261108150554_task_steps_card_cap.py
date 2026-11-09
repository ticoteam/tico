"""A stage's card cap: task_steps.card_cap, the most cards the stage holds at once (D68). NULL is no cap; 0 is a stage that
takes no card from a bot and whose cards wake no one (D69, "On ice"). A stage's cap is set by a person, and no migration
or setup run changes a cap that a person has set, so this adds the column and fills nothing."""


def up(conn):
    columns = {row[1] for row in conn.execute("PRAGMA table_info(task_steps)")}
    if columns and "card_cap" not in columns:
        conn.execute("ALTER TABLE task_steps ADD COLUMN card_cap INTEGER")
