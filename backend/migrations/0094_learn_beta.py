"""Learn keeps items for Update turns, alongside the old packet receipts."""

import re

FOREIGN_KEYS_OFF = True


def _rebuild(conn, table, sql):
    objects = conn.execute("SELECT sql FROM sqlite_master WHERE tbl_name=? AND type IN ('index','trigger') "
                           "AND sql IS NOT NULL", (table,)).fetchall()
    temporary = table + "_new"
    sql = re.sub(r"CREATE TABLE\s+(?:IF NOT EXISTS\s+)?[\w\"`\[\]]+", "CREATE TABLE " + temporary, sql, count=1,
                 flags=re.I)
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
    conn.execute("CREATE TABLE IF NOT EXISTS learning_runs("
                 "id TEXT PRIMARY KEY, night TEXT NOT NULL, status TEXT NOT NULL, started_at TEXT NOT NULL, finished_at TEXT, "
                 "read_count INTEGER NOT NULL DEFAULT 0, routed_count INTEGER NOT NULL DEFAULT 0, "
                 "detail TEXT NOT NULL DEFAULT '{}')")
    sql = conn.execute("SELECT sql FROM sqlite_master WHERE name='learning_runs'").fetchone()[0]
    # Custom step frequencies can produce more than one run on a day.
    revised = re.sub(r"(night\s+TEXT\s+NOT\s+NULL)\s+UNIQUE", r"\1", sql, flags=re.I)
    if revised != sql:
        _rebuild(conn, "learning_runs", revised)
    conn.execute("CREATE INDEX IF NOT EXISTS learning_runs_night ON learning_runs(night,started_at)")
    conn.execute("CREATE TABLE IF NOT EXISTS learning_items("
                 "run_id TEXT NOT NULL, item_key TEXT NOT NULL, source TEXT NOT NULL, ref TEXT NOT NULL, "
                 "author TEXT NOT NULL DEFAULT '', label TEXT NOT NULL, text TEXT NOT NULL, created_at TEXT NOT NULL, "
                 "learnable REAL, scores TEXT NOT NULL DEFAULT '{}', PRIMARY KEY(run_id,item_key))")
    conn.execute("CREATE TABLE IF NOT EXISTS learning_deliveries("
                 "run_id TEXT NOT NULL, recipient TEXT NOT NULL, task_id TEXT, item_keys TEXT NOT NULL, "
                 "created_at TEXT NOT NULL, PRIMARY KEY(run_id,recipient))")
    columns = {row[1]: row for row in conn.execute("PRAGMA table_info(learning_deliveries)")}
    if columns["task_id"][3]:
        sql = conn.execute("SELECT sql FROM sqlite_master WHERE name='learning_deliveries'").fetchone()[0]
        _rebuild(conn, "learning_deliveries", re.sub(r"(task_id\s+TEXT)\s+NOT\s+NULL", r"\1", sql, flags=re.I))
    for name, declaration in (("update_batch_id", "TEXT"), ("message_id", "TEXT"), ("sent_item_keys", "TEXT"),
                              ("sent_at", "TEXT"),
                              ("learnings_saved", "INTEGER CHECK(learnings_saved BETWEEN 0 AND 15)"),
                              ("settled_at", "TEXT"), ("expired_at", "TEXT")):
        if name not in columns:
            conn.execute(f"ALTER TABLE learning_deliveries ADD COLUMN {name} {declaration}")
    conn.execute("CREATE INDEX IF NOT EXISTS learning_deliveries_pending ON learning_deliveries(recipient,update_batch_id) "
                 "WHERE task_id IS NULL")
    conn.execute("CREATE INDEX IF NOT EXISTS learning_deliveries_batch ON learning_deliveries(update_batch_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS learning_deliveries_expiry ON learning_deliveries(recipient,created_at) "
                 "WHERE task_id IS NULL AND update_batch_id IS NULL AND expired_at IS NULL")
    conn.execute("CREATE TABLE IF NOT EXISTS learning_cursors(source TEXT PRIMARY KEY,last_ts TEXT NOT NULL)")
