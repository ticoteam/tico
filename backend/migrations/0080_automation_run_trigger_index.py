"""Two indexes on the automation run table, built here as well as in automations.py's schema: a new file runs the
migration files before the module's CREATE (migration 0050 builds its table the same way), so an index would otherwise
have no table to sit on. Both are IF NOT EXISTS, so an install that has them keeps them as they are.

automation_runs_trigger: a message's run is found by its automation and trigger ref (`mail:<mailbox>:<msg_id>`,
`slack:...`), so a message that is offered again starts nothing new (backend/automations.py `queue`). The run outlives
the message's row (mail_messages is swept after retention), so this lookup must not scan the automation's runs.

automation_runs_active: the claim counts an automation's runs in flight (running or waiting, no batch wake time). Those
are few next to its whole history, so this partial index holds only them and the count (automations.py OCCUPIED) is a
seek rather than a scan of every run the automation ever had. Its WHERE is the one OCCUPIED uses, so SQLite picks it.
"""

TABLE = ("CREATE TABLE IF NOT EXISTS automation_runs(id TEXT PRIMARY KEY, automation_id TEXT NOT NULL, "
         "version INTEGER NOT NULL, trigger_ref TEXT NOT NULL DEFAULT '', started_at TEXT NOT NULL, finished_at TEXT, "
         "status TEXT NOT NULL, cost REAL NOT NULL DEFAULT 0, saved_state TEXT, claimed_at TEXT, wake_at TEXT, "
         "chain TEXT NOT NULL DEFAULT '[]')")
TRIGGER_INDEX = "CREATE INDEX IF NOT EXISTS automation_runs_trigger ON automation_runs(automation_id, trigger_ref)"
ACTIVE_INDEX = ("CREATE INDEX IF NOT EXISTS automation_runs_active ON automation_runs(automation_id) "
                "WHERE status IN ('running','waiting') AND wake_at IS NULL")


def up(conn):
    conn.execute(TABLE)
    conn.execute(TRIGGER_INDEX)
    conn.execute(ACTIVE_INDEX)
