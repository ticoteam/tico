"""Statement uploads (backend/finance/statement_upload.py): a CSV, OFX or QFX statement for a bank or card money account
Tico cannot connect. Its rows enter the feed as fin_feed_txns carrying upload_id, and post through the same path as a
synced row. The file's content hash is kept per money account, so the same file posts once (fin_statement_uploads).
A preview keeps only the hash of the token it hands out, so a post can check it posts what was shown
(fin_statement_previews); preview rows expire.

fin_feed_txns.upload_id is also in store/schema.ADDED, which `create` adds on a new install (there the feed table does
not exist yet when this runs, so only the two new tables come from here). An install whose feed table exists gets the
column here, with the same type, so both paths end in one shape. The column has no index: its one reader (undo) asks by
money account too, which fin_feed_txns_account covers. Idempotent: IF NOT EXISTS, and the column is added only when the
table lacks it.
"""

TABLES = (
    """CREATE TABLE IF NOT EXISTS fin_statement_uploads (
  id TEXT PRIMARY KEY, book_id TEXT NOT NULL REFERENCES fin_books(id),
  money_account_id TEXT NOT NULL REFERENCES fin_money_accounts(id), file_hash TEXT NOT NULL,
  file_name TEXT NOT NULL DEFAULT '', file_format TEXT NOT NULL CHECK (file_format IN ('csv','ofx')),
  start_date TEXT NOT NULL, end_date TEXT NOT NULL, opening_balance BIGINT, closing_balance BIGINT,
  ends_at BIGINT NOT NULL, off_by BIGINT NOT NULL DEFAULT 0,
  row_count INTEGER NOT NULL, new_count INTEGER NOT NULL, duplicate_count INTEGER NOT NULL,
  skipped_count INTEGER NOT NULL DEFAULT 0, statement_id TEXT, action_id TEXT NOT NULL,
  created_at TEXT NOT NULL, created_by TEXT NOT NULL, undone_at TEXT, undone_by TEXT)""",
    "CREATE INDEX IF NOT EXISTS fin_statement_uploads_account ON fin_statement_uploads(money_account_id, created_at)",
    "CREATE INDEX IF NOT EXISTS fin_statement_uploads_hash ON fin_statement_uploads(money_account_id, file_hash)",
    """CREATE TABLE IF NOT EXISTS fin_statement_previews (
  token_hash TEXT PRIMARY KEY, book_id TEXT NOT NULL REFERENCES fin_books(id),
  money_account_id TEXT NOT NULL REFERENCES fin_money_accounts(id), file_hash TEXT NOT NULL,
  new_digest TEXT NOT NULL, created_at TEXT NOT NULL, created_by TEXT NOT NULL, expires_at TEXT NOT NULL)""",
    "CREATE INDEX IF NOT EXISTS fin_statement_previews_expiry ON fin_statement_previews(expires_at)",
)


def up(conn):
    columns = {row[1] for row in conn.execute("PRAGMA table_info(fin_feed_txns)")}
    if columns and "upload_id" not in columns:  # a fresh install gets the column from store/schema.create
        conn.execute("ALTER TABLE fin_feed_txns ADD COLUMN upload_id TEXT")
    for statement in TABLES:
        conn.execute(statement)
