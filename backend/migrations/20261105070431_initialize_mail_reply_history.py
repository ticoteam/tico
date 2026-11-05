"""Build reply-history facts on new installs before the connector's module schema runs.

Migrations run before Store creates connector tables. Existing installs keep their rows; a new install needs the
whole table here so the RFC Message-ID lookup index is installed once by the migration authority as well.
"""

TABLE = ("CREATE TABLE IF NOT EXISTS mail_messages("
         "mailbox TEXT NOT NULL, msg_id TEXT NOT NULL, thread_id TEXT NOT NULL DEFAULT '', "
         "epoch INTEGER NOT NULL, date TEXT NOT NULL, from_addr TEXT NOT NULL, from_header TEXT NOT NULL DEFAULT '', "
         "to_addresses TEXT NOT NULL, cc TEXT NOT NULL, subject TEXT NOT NULL, snippet TEXT NOT NULL, "
         "labels TEXT NOT NULL, body TEXT NOT NULL, body_truncated INTEGER NOT NULL DEFAULT 0, "
         "attachments TEXT NOT NULL, list_id TEXT NOT NULL DEFAULT '', is_internal INTEGER NOT NULL DEFAULT 0, "
         "has_unsubscribe INTEGER NOT NULL DEFAULT 0, rule_hits TEXT NOT NULL DEFAULT '[]', "
         "updated_at TEXT NOT NULL, deleted_at TEXT, PRIMARY KEY(mailbox,msg_id))")
INDEX = "CREATE INDEX IF NOT EXISTS mail_messages_rfc_id ON mail_messages(mailbox,json_extract(send_facts,'$.message-id'))"


def up(conn):
    conn.execute(TABLE)
    if "send_facts" not in {row[1] for row in conn.execute("PRAGMA table_info(mail_messages)")}:
        conn.execute("ALTER TABLE mail_messages ADD COLUMN send_facts TEXT NOT NULL DEFAULT '{}'")
    conn.execute(INDEX)
