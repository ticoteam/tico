"""Finance's subscriptions (backend/finance/subscriptions.py): a recurring charge a book pays, one row per vendor and the
card or money account it is paid from. Found from repeat charges (status suggested) and confirmed by the Bookkeeper
(active, ignored or cancelled). Integer cents, ISO days, UTC times.

`prior` is the fields before the latest confirmation, which subscription_undo puts back (NULL: nothing to undo). `source_key`
is the card or money account the charge left from, one value, so the unique key holds where a NULL would not. Idempotent:
a re-run adds only the table and index it lacks.
"""

SUBSCRIPTIONS = """
CREATE TABLE IF NOT EXISTS fin_subscriptions (
 id TEXT PRIMARY KEY,
 book_id TEXT NOT NULL,
 vendor_key TEXT NOT NULL,
 money_account_id TEXT,
 card_id TEXT,
 source_key TEXT NOT NULL DEFAULT '',
 cadence TEXT NOT NULL DEFAULT 'monthly' CHECK (cadence IN ('monthly','annual')),
 amount INTEGER NOT NULL DEFAULT 0,
 seats INTEGER,
 status TEXT NOT NULL DEFAULT 'suggested' CHECK (status IN ('suggested','active','ignored','cancelled')),
 next_charge_on TEXT,
 last_charge_on TEXT,
 last_charge_id TEXT,
 last_amount INTEGER NOT NULL DEFAULT 0,
 first_charge_on TEXT,
 confirmed_at TEXT,
 confirmed_by TEXT,
 prior TEXT,
 created_at TEXT NOT NULL,
 created_by TEXT NOT NULL,
 updated_at TEXT NOT NULL,
 UNIQUE (book_id, vendor_key, source_key)
)
"""

INDEX = "CREATE INDEX IF NOT EXISTS fin_subscriptions_book_status ON fin_subscriptions (book_id, status)"


def up(conn):
    conn.execute(SUBSCRIPTIONS)
    conn.execute(INDEX)
