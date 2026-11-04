"""Install-owned suggestion receipts; no suggestion text is exposed through SQL views."""


def up(conn):
    conn.execute("CREATE TABLE IF NOT EXISTS suggestions("
                 "id TEXT PRIMARY KEY, fingerprint TEXT NOT NULL UNIQUE, owner_role TEXT NOT NULL, owner_bot_id TEXT, "
                 "title TEXT NOT NULL, body TEXT NOT NULL, condition TEXT NOT NULL, cooldown_days INTEGER NOT NULL, "
                 "status TEXT NOT NULL DEFAULT 'new' CHECK(status IN "
                 "('new','sent','acted','dismissed','expired','condition_false')), update_batch_id TEXT, "
                 "delivered_at TEXT, cooldown_until TEXT, outcome TEXT CHECK(outcome IN "
                 "('delivered','acted','dismissed','expired','condition_false')), outcome_reported_at TEXT, "
                 "created_at TEXT NOT NULL, updated_at TEXT NOT NULL)")
    conn.execute("CREATE INDEX IF NOT EXISTS suggestions_pending ON suggestions(status,owner_role,created_at)")
    conn.execute("CREATE INDEX IF NOT EXISTS suggestions_batch ON suggestions(owner_bot_id,update_batch_id)")
