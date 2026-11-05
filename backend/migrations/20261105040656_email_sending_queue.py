"""One sending policy and a durable outbox; preserve existing, scoped authorizations."""

import json


def up(conn):
    tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    first = "email_sending_policies" not in tables
    statements = (
        "CREATE TABLE IF NOT EXISTS email_sending_policies(scope TEXT PRIMARY KEY, settings TEXT NOT NULL, "
        "updated_at TEXT NOT NULL, updated_by TEXT NOT NULL)",
        "CREATE TABLE IF NOT EXISTS email_sends(id TEXT PRIMARY KEY, mailbox TEXT NOT NULL, actor TEXT NOT NULL, bot TEXT NOT NULL, via TEXT NOT NULL, "
        "automation_action_id TEXT, content_sha256 TEXT NOT NULL, idempotency_key TEXT NOT NULL UNIQUE, "
        "recipients TEXT NOT NULL, attachments TEXT NOT NULL, subject TEXT NOT NULL, body_sha256 TEXT NOT NULL, "
        "raw TEXT NOT NULL, draft_id TEXT NOT NULL, thread_id TEXT NOT NULL, reply_to_id TEXT NOT NULL, approval_id TEXT NOT NULL, "
        "classification TEXT NOT NULL, decision TEXT NOT NULL, state TEXT NOT NULL, created_at TEXT NOT NULL, "
        "send_after TEXT NOT NULL, claimed_at TEXT, provider_attempted_at TEXT, runner_id TEXT, finished_at TEXT, provider_message_id TEXT, "
        "detail TEXT NOT NULL DEFAULT '')",
        "CREATE INDEX IF NOT EXISTS email_sends_dispatch ON email_sends(state,send_after,id)",
        "CREATE INDEX IF NOT EXISTS email_sends_mailbox ON email_sends(mailbox,created_at,id)",
        "CREATE INDEX IF NOT EXISTS email_sends_actor ON email_sends(actor,created_at)",
        "CREATE INDEX IF NOT EXISTS email_sends_day ON email_sends(substr(coalesce(provider_attempted_at,claimed_at,created_at),1,10),state)",
        "CREATE INDEX IF NOT EXISTS email_sends_pacing ON email_sends(mailbox,coalesce(provider_attempted_at,claimed_at) DESC) "
        "WHERE state IN ('sent','dispatching','unknown')",
        "CREATE TABLE IF NOT EXISTS email_suppressed_recipients(mailbox TEXT NOT NULL, address TEXT NOT NULL, reason TEXT NOT NULL, "
        "created_at TEXT NOT NULL, PRIMARY KEY(mailbox,address))",
        # Snapshot existing automation ids, never an inbox-wide permission that new automations could inherit.
        "CREATE TABLE IF NOT EXISTS email_legacy_automations(automation_id TEXT PRIMARY KEY, owner_id TEXT NOT NULL, "
        "version INTEGER NOT NULL)",
    )
    for statement in statements:
        conn.execute(statement)
    if "mail_messages" in tables and "send_facts" not in {row[1] for row in conn.execute("PRAGMA table_info(mail_messages)")}:
        conn.execute("ALTER TABLE mail_messages ADD COLUMN send_facts TEXT NOT NULL DEFAULT '{}'")
    if not first:
        return  # Adopting the existing schema never restores an authorization a person has since tightened.
    for row in conn.execute("SELECT * FROM bot_mail_settings") if "bot_mail_settings" in tables else []:
        settings = {"mode": "on_their_own" if row["outbound_send"] else "ask_first", "legacy": True, "daily_cap": 20}
        conn.execute("INSERT INTO email_sending_policies VALUES(?,?,?,?)",
                     ("bot:" + row["bot"], json.dumps(settings), row["updated_at"], "migration"))
    if "automations" in tables:
        conn.execute("INSERT INTO email_legacy_automations SELECT id,owner_id,version FROM automations "
                     "WHERE deleted_at IS NULL")
