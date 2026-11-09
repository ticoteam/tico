"""Finance's receipts (backend/finance/receipts.py): one row per receipt file that lands in Finance, with what its text
reads (amount, date, vendor) and the bank or card row it matches. fin_attachments stays the truth for what is attached;
this table adds the read and the suggestion, and remembers what an accept changed so its undo can put it back.

`status`: unread (no read yet), unmatched (read, no match), suggested (match_feed_id, confidence), attached (filed to a
subject), rejected (a suggestion was refused; `rejected_feed_ids` keeps the rows it was refused for). `prior_*` is the
state before the latest accept, which receipt_match_undo restores. Idempotent: a re-run adds only what is missing."""

RECEIPTS = """
CREATE TABLE IF NOT EXISTS fin_receipts (
 id TEXT PRIMARY KEY,
 book_id TEXT NOT NULL REFERENCES fin_books(id),
 file_id TEXT NOT NULL,
 read_amount BIGINT,
 read_date TEXT,
 read_vendor_name TEXT NOT NULL DEFAULT '',
 read_vendor_key TEXT NOT NULL DEFAULT '',
 read_by TEXT NOT NULL DEFAULT '',
 match_feed_id TEXT,
 confidence REAL,
 status TEXT NOT NULL DEFAULT 'unread'
   CHECK (status IN ('unread','unmatched','suggested','attached','rejected')),
 rejected_feed_ids TEXT NOT NULL DEFAULT '[]',
 attachment_id TEXT,
 prior_status TEXT,
 prior_feed_id TEXT,
 prior_confidence REAL,
 created_by TEXT NOT NULL,
 decided_at TEXT,
 decided_by TEXT,
 created_at TEXT NOT NULL,
 updated_at TEXT NOT NULL,
 UNIQUE (book_id, file_id)
)
"""

INDEXES = (
    "CREATE INDEX IF NOT EXISTS fin_receipts_status ON fin_receipts (book_id, status, created_at)",
    "CREATE INDEX IF NOT EXISTS fin_receipts_feed ON fin_receipts (match_feed_id)",
)


def up(conn):
    conn.execute(RECEIPTS)
    for statement in INDEXES:
        conn.execute(statement)
