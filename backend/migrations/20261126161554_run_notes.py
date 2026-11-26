"""A run's pipeline and stage notes, as its record shows them (backend/pipeline_notes.py): `notes_offered`, the lists
its claim carried ("pipeline", "stage" or "pipeline,stage"), and `notes_sent`, what the runner says the turn's prompt
did with each (JSON, runner-rs/src/prompt.rs notes_sent). Both NULL on runs from before, and on runs with no notes."""


def up(conn):
    have = {row[1] for row in conn.execute("PRAGMA table_info(attempts)")}
    for column in ("notes_offered", "notes_sent"):
        if column not in have:
            conn.execute(f"ALTER TABLE attempts ADD COLUMN {column} TEXT")
