"""Finance's combined build (backend/finance): covered history, prepaid bill lines, refunds, invoice reminders and invoice
emails, cancel asks on unknown payments, a card's merchant, and the books' full re-read.

- fin_feed_txns may hold `covered` (a settled row dated in a raw-totals month, spec §4.1). SQLite cannot widen a CHECK
  in place, so the table is rebuilt from its own definition with the wider list, keeping its rows, indexes and columns.
  It also takes refund_of, the money-out row a refund reverses (backend/finance/refunds.py), and an index on it.
- fin_bill_lines takes prepaid_start and prepaid_months, and fin_prepaid_postings records each month a prepaid line was
  expensed (backend/finance/prepaids.py).
- fin_invoice_reminders: one reminder per invoice and stage (backend/finance/documents.py invoice_remind).
- fin_invoices and fin_invoice_reminders take an email's send approval and delivery report (backend/finance/delivery.py).
- fin_payments takes cancel_asked_at and cancel_asked_by: a finance admin's cancel of an unknown payment, waiting for
  Mercury's lookup by its key (backend/finance/payments.py).
- fin_cards takes merchant, the merchant a card was asked for (backend/finance/cards.py).
- fin_external_refs takes seen_gen, which a full re-read of the books marks (backend/finance/mirror.py).

On a new file the Finance tables do not exist yet when this runs: each step skips a table that is absent, and cloud step
64 (backend/finance/store/schema.py create) makes the new shape. Every step checks what is there, so a file that already
has a change keeps it."""

FOREIGN_KEYS_OFF = True

NARROW = "CHECK (status IN ('new','matched','posted','ignored'))"
WIDE = "CHECK (status IN ('new','matched','posted','ignored','covered'))"

COLUMNS = (
    ("fin_feed_txns", "refund_of", "TEXT"),
    ("fin_bill_lines", "prepaid_start", "TEXT"),
    ("fin_bill_lines", "prepaid_months", "INTEGER"),
    ("fin_invoices", "send_approval_id", "TEXT"),
    ("fin_invoices", "send_to", "TEXT"),
    ("fin_invoices", "send_text", "TEXT"),
    ("fin_invoices", "delivered_at", "TEXT"),
    ("fin_invoices", "delivery_reason", "TEXT"),
    ("fin_payments", "cancel_asked_at", "TEXT"),
    ("fin_payments", "cancel_asked_by", "TEXT NOT NULL DEFAULT ''"),
    ("fin_cards", "merchant", "TEXT NOT NULL DEFAULT ''"),
    ("fin_external_refs", "seen_gen", "INTEGER"),
)

# Each new table, made once the tables it refers to exist.
TABLES = (
    (("fin_bill_lines", "fin_entries"),
     "CREATE TABLE IF NOT EXISTS fin_prepaid_postings ("
     "bill_line_id TEXT NOT NULL REFERENCES fin_bill_lines(id), month TEXT NOT NULL, "
     "amount BIGINT NOT NULL CHECK (amount > 0), entry_id TEXT NOT NULL REFERENCES fin_entries(id), "
     "action_id TEXT, created_at TEXT NOT NULL, created_by TEXT NOT NULL, PRIMARY KEY (bill_line_id, month))"),
    (("fin_invoices",),
     "CREATE TABLE IF NOT EXISTS fin_invoice_reminders ("
     "id TEXT PRIMARY KEY, invoice_id TEXT NOT NULL REFERENCES fin_invoices(id), stage INTEGER NOT NULL CHECK (stage > 0), "
     "text TEXT NOT NULL, sent_to TEXT NOT NULL, action_id TEXT, created_at TEXT NOT NULL, created_by TEXT NOT NULL, "
     "send_approval_id TEXT, delivered_at TEXT, delivery_reason TEXT, UNIQUE (invoice_id, stage))"),
)


def _tables(conn):
    return {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}


def _widen_feed(conn):
    """fin_feed_txns rebuilt with `covered` in its status CHECK: the same columns in the same order, its rows copied,
    its indexes made again (a dropped table takes its indexes with it)."""
    sql = conn.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='fin_feed_txns'").fetchone()[0]
    if "'covered'" in sql:
        return
    if NARROW not in sql:
        raise RuntimeError("fin_feed_txns has a status CHECK this migration does not know: " + sql)
    head = "CREATE TABLE fin_feed_txns"
    if not sql.startswith(head):
        raise RuntimeError("fin_feed_txns has a definition this migration does not know: " + sql[:80])
    indexes = [row[0] for row in conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='index' AND tbl_name='fin_feed_txns' AND sql IS NOT NULL")]
    columns = ",".join(f'"{row[1]}"' for row in conn.execute("PRAGMA table_info(fin_feed_txns)"))
    conn.execute("CREATE TABLE fin_feed_txns_widened" + sql[len(head):].replace(NARROW, WIDE, 1))
    conn.execute(f"INSERT INTO fin_feed_txns_widened({columns}) SELECT {columns} FROM fin_feed_txns")
    conn.execute("DROP TABLE fin_feed_txns")
    conn.execute("ALTER TABLE fin_feed_txns_widened RENAME TO fin_feed_txns")
    for statement in indexes:
        conn.execute(statement)


def up(conn):
    found = _tables(conn)
    if "fin_feed_txns" in found:
        _widen_feed(conn)
    for table, column, ddl in COLUMNS:
        if table in found and column not in {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}")
    for needs, statement in TABLES:
        if set(needs) <= found:
            conn.execute(statement)
    if "fin_feed_txns" in found:
        conn.execute("CREATE INDEX IF NOT EXISTS fin_feed_txns_refund ON fin_feed_txns(refund_of)")
