"""Feedback takes an ask as its subject (backend/answers.py, docs/pipelines-v3.md "Thumbs down, and rules"): a 👍 or 👎
on a requirement with a person on it, in one visit of one task. Its subject id is `<visit id>:<requirement id>`.

SQLite cannot change a CHECK in place, so `feedbacks` is rebuilt with `ask` added. Its columns, rows, rowids, indexes
and unique keys carry over. A table that already takes `ask` is left as it is.
"""

import re

FOREIGN_KEYS_OFF = True

SUBJECTS = re.compile(r"subject_type\s+IN\s*\(\s*'message'\s*,\s*'task'\s*,\s*'approval'\s*\)", re.I)
WITH_ASK = "subject_type IN ('message','task','approval','ask')"


def _rebuild(conn, table, sql):
    objects = conn.execute(
        "SELECT sql FROM sqlite_master WHERE tbl_name=? AND type IN ('index','trigger') AND sql IS NOT NULL", (table,)
    ).fetchall()
    temporary = table + "_new"
    sql = re.sub(
        r"CREATE TABLE\s+(?:IF NOT EXISTS\s+)?[\w\"`\[\]]+", "CREATE TABLE " + temporary, sql, count=1, flags=re.I
    )
    conn.execute(sql)
    columns = ",".join('"' + row[1].replace('"', '""') + '"' for row in conn.execute(f"PRAGMA table_info({table})"))
    conn.execute(f"INSERT INTO {temporary}(rowid,{columns}) SELECT rowid,{columns} FROM {table}")
    conn.execute(f"DROP TABLE {table}")
    legacy = conn.execute("PRAGMA legacy_alter_table").fetchone()[0]
    conn.execute("PRAGMA legacy_alter_table=ON")
    try:
        conn.execute(f"ALTER TABLE {temporary} RENAME TO {table}")
    finally:
        conn.execute(f"PRAGMA legacy_alter_table={'ON' if legacy else 'OFF'}")
    for (statement,) in objects:
        conn.execute(statement)


def up(conn):
    row = conn.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='feedbacks'").fetchone()
    if not row:
        return
    revised = SUBJECTS.sub(WITH_ASK, row[0], count=1)
    if revised != row[0]:
        _rebuild(conn, "feedbacks", revised)
