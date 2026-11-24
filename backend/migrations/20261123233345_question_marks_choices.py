"""A ❓'s suggested answers (backend/question_marks.py): `question_marks.choices`, up to three short replies the bot
offers, as a JSON list. The person taps one (it is sent as their reply) or types their own. NULL: a free-text ❓, from
before or with none.

Pending migrations may run newest first on a running company, so the table may not be there yet: then it is made by
running 20261117033356_question_marks.sql itself (every statement IF NOT EXISTS, so running that file later changes
nothing), and the column is added to it.
"""

from pathlib import Path

from backend.migrations import statements

TABLE = Path(__file__).with_name("20261117033356_question_marks.sql")


def up(conn):
    if not conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='question_marks'").fetchone():
        for statement in statements(TABLE.read_text(encoding="utf-8")):
            conn.execute(statement)
    if "choices" not in {row[1] for row in conn.execute("PRAGMA table_info(question_marks)")}:
        conn.execute("ALTER TABLE question_marks ADD COLUMN choices TEXT")
