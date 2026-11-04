"""A message's run is found by its automation and trigger ref (`mail:<mailbox>:<msg_id>`, `slack:...`), so a message
that is offered again starts nothing new (backend/automations.py `queue`). The run outlives the message's row
(mail_messages is swept after retention), so this lookup must not scan the automation's runs: this index makes it a
seek.

The table is built here as well, the way migration 0050 builds its own: a new file runs the migration files before the
module's CREATE, so the index would have no table to sit on. The definition is the one automations.py makes; both are
IF NOT EXISTS, so an install that has the table keeps it as it is.
"""

TABLE = ("CREATE TABLE IF NOT EXISTS automation_runs(id TEXT PRIMARY KEY, automation_id TEXT NOT NULL, "
         "version INTEGER NOT NULL, trigger_ref TEXT NOT NULL DEFAULT '', started_at TEXT NOT NULL, finished_at TEXT, "
         "status TEXT NOT NULL, cost REAL NOT NULL DEFAULT 0, saved_state TEXT, claimed_at TEXT, wake_at TEXT, "
         "chain TEXT NOT NULL DEFAULT '[]')")
INDEX = "CREATE INDEX IF NOT EXISTS automation_runs_trigger ON automation_runs(automation_id, trigger_ref)"


def up(conn):
    conn.execute(TABLE)
    conn.execute(INDEX)
