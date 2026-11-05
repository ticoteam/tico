"""One sending policy and a durable outbox; preserve existing, scoped authorizations."""

import json


def up(conn):
    statements = (
        "CREATE TABLE email_sending_policies(scope TEXT PRIMARY KEY, settings TEXT NOT NULL, "
        "updated_at TEXT NOT NULL, updated_by TEXT NOT NULL)",
        "CREATE TABLE email_sends(id TEXT PRIMARY KEY, mailbox TEXT NOT NULL, actor TEXT NOT NULL, bot TEXT NOT NULL, "
        "automation_action_id TEXT, content_sha256 TEXT NOT NULL, idempotency_key TEXT NOT NULL UNIQUE, "
        "recipients TEXT NOT NULL, attachments TEXT NOT NULL, subject TEXT NOT NULL, body_sha256 TEXT NOT NULL, "
        "raw TEXT NOT NULL, thread_id TEXT NOT NULL, reply_to_id TEXT NOT NULL, approval_id TEXT NOT NULL, "
        "classification TEXT NOT NULL, decision TEXT NOT NULL, state TEXT NOT NULL, created_at TEXT NOT NULL, "
        "send_after TEXT NOT NULL, claimed_at TEXT, provider_attempted_at TEXT, runner_id TEXT, finished_at TEXT, provider_message_id TEXT, "
        "detail TEXT NOT NULL DEFAULT '')",
        "CREATE INDEX email_sends_dispatch ON email_sends(state,send_after,id)",
        "CREATE INDEX email_sends_mailbox ON email_sends(mailbox,created_at,id)",
        "CREATE INDEX email_sends_actor ON email_sends(actor,created_at)",
        "CREATE TABLE email_suppressed_recipients(mailbox TEXT NOT NULL, address TEXT NOT NULL, reason TEXT NOT NULL, "
        "created_at TEXT NOT NULL, PRIMARY KEY(mailbox,address))",
        # Snapshot existing automation ids, never an inbox-wide permission that new automations could inherit.
        "CREATE TABLE email_legacy_automations(automation_id TEXT PRIMARY KEY, owner_id TEXT NOT NULL, "
        "version INTEGER NOT NULL)",
    )
    for statement in statements:
        conn.execute(statement)
    tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    if "mail_messages" in tables:
        conn.execute("ALTER TABLE mail_messages ADD COLUMN send_facts TEXT NOT NULL DEFAULT '{}'")
    for row in conn.execute("SELECT * FROM bot_mail_settings") if "bot_mail_settings" in tables else []:
        settings = {"mode": "on_their_own" if row["outbound_send"] else "ask_first", "legacy": True, "daily_cap": 20}
        conn.execute("INSERT INTO email_sending_policies VALUES(?,?,?,?)",
                     ("bot:" + row["bot"], json.dumps(settings), row["updated_at"], "migration"))
    if "automations" in tables:
        conn.execute("INSERT INTO email_legacy_automations SELECT id,owner_id,version FROM automations "
                     "WHERE deleted_at IS NULL")
