"""An independent schema change; the migration runner owns the transaction."""

# For table rebuilds, set FOREIGN_KEYS_OFF = True here. The runner disables foreign keys
# before BEGIN, checks references before COMMIT, and restores the setting afterward.


def up(conn):
    columns = {row[1] for row in conn.execute("PRAGMA table_info(email_sends)")}
    if columns:
        for name in ("operation_key", "lease_id", "checked_at"):
            if name not in columns:
                conn.execute(f"ALTER TABLE email_sends ADD COLUMN {name} TEXT NOT NULL DEFAULT ''")
        if "calendar_action_id" not in columns:
            conn.execute("ALTER TABLE email_sends ADD COLUMN calendar_action_id TEXT")
        conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS email_sends_operation ON email_sends(actor,operation_key) "
                     "WHERE operation_key!=''")
        conn.execute("CREATE INDEX IF NOT EXISTS email_sends_waiting ON email_sends(mailbox,state,checked_at)")
    conn.execute("CREATE TABLE IF NOT EXISTS email_send_previews(send_id TEXT NOT NULL, actor TEXT NOT NULL, "
                 "content_sha256 TEXT NOT NULL, shown_at TEXT NOT NULL, PRIMARY KEY(send_id,actor))")
    conn.execute("CREATE TABLE IF NOT EXISTS email_send_operations(actor TEXT NOT NULL, operation_key TEXT NOT NULL, "
                 "send_id TEXT NOT NULL, PRIMARY KEY(actor,operation_key))")
    if "mail_messages" in {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}:
        if "send_facts" in {row[1] for row in conn.execute("PRAGMA table_info(mail_messages)")}:
            conn.execute("CREATE INDEX IF NOT EXISTS mail_messages_rfc_id ON mail_messages(mailbox,json_extract(send_facts,'$.message-id'))")
