"""A matter's own dates (backend/risk.py): the earliest it may be done and when it is due.

- `matters.starts_at`: not before. The earliest the matter's step may be done ("records may be produced no earlier than
  November 19, 10 AM"); never a deadline.
- `matters.due_at`: due by. The deadline.
- `matters.dates_label`: what the dates are for, in a few words ("Produce records").

Each date is a day (YYYY-MM-DD, the whole day) or a UTC time (YYYY-MM-DDTHH:MM:SSZ), or null. Every column is added
with ALTER TABLE ... ADD COLUMN; no table is rebuilt. Running it again changes nothing.
"""


def up(conn):
    columns = {row[1] for row in conn.execute("PRAGMA table_info(matters)")}
    for column, ddl in (
        ("starts_at", "TEXT"),
        ("due_at", "TEXT"),
        ("dates_label", "TEXT NOT NULL DEFAULT ''"),
    ):
        if column not in columns:
            conn.execute(f"ALTER TABLE matters ADD COLUMN {column} {ddl}")
