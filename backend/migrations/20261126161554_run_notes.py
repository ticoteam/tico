"""A run's pipeline and stage notes, as its record shows them (backend/pipeline_notes.py), in a table of its own so the
change adds no column to `attempts` (another pending migration adds one there, and two such migrations would order its
columns by which ran first): `offered`, the lists the claim carried ("pipeline", "stage" or "pipeline,stage"), and
`sent`, what the runner says the turn's prompt did with each (JSON, runner-rs/src/prompt.rs notes_sent). A run with no
row had no notes, or came before this."""


def up(conn):
    conn.execute(
        "CREATE TABLE IF NOT EXISTS attempt_notes(attempt_id TEXT PRIMARY KEY REFERENCES attempts(id), offered TEXT, "
        "sent TEXT)"
    )
