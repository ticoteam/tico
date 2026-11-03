"""Persist task appointments and the reminder-style delivery ledger."""


def up(conn):
    if not conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='tasks'").fetchone():
        return
    columns = {r[1] for r in conn.execute("PRAGMA table_info(tasks)")}
    for name in ("check_back_at", "check_back_note"):
        if name not in columns:
            conn.execute("ALTER TABLE tasks ADD COLUMN " + name + " TEXT")
    conn.execute("CREATE TABLE IF NOT EXISTS task_check_back_wakes("
                 "task_id TEXT NOT NULL REFERENCES tasks(id) ON DELETE CASCADE, check_back_at TEXT NOT NULL, "
                 "sent_at TEXT NOT NULL, message_id TEXT REFERENCES messages(id) ON DELETE SET NULL, "
                 "PRIMARY KEY(task_id, check_back_at))")
    conn.execute("CREATE INDEX IF NOT EXISTS task_check_back_wakes_sent ON task_check_back_wakes(sent_at,task_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS tasks_check_back_due ON tasks(check_back_at,id) "
                 "WHERE status IN ('open','doing','waiting','review','ready','done','declined')")
