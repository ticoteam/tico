"""One table for the stored retry answers and the retained operation bindings (backend/store.py's `idempotency`).

Before this file, the answers (swept after 48 hours) lived in `idempotency`, and three tables kept the operation bindings
that are never swept: `github_product_repo_operations` (backend/github_app.py), `invite_sends` (backend/invites.py) and
`github_review_operations` (backend/github_reviews.py). Their rows move into `idempotency`:

- an answer keeps its rowid, binding NULL and state completed: a retry after the upgrade replays it as before;
- a binding sets `binding` to the operation's name, `subject` to what it is about (an invite's person id, a product
  repository's org/name, empty for a review) and `state` to pending, completed or refused (sending and sent become
  pending and completed, rejected becomes refused, a review with no response is pending);
- a product repository's answer that was only in `idempotency` (the earlier implementation's receipt, which create
  copied into a binding on retry) becomes its binding, so the create is still never repeated.

Where a binding and an answer share a key (the invite routes once stored both under one key), the binding wins: it
carries the send's state, and the answer was a replay of the record the binding already makes.

The old answer table is renamed aside (legacy ALTER TABLE rules, so no other schema object is reparsed or rewritten), the
rows are copied in one pass each, and the old tables go with their indexes and triggers. The `idempotency_messages`
triggers are made again at the end of the same start (Store.initialize, idempotency_messages.ensure); that index names
answers by key, so its rows stay valid, less those of the answers that became bindings. Every statement runs in the
migration's one transaction, so a failure leaves the old layout as it was. A file that already has the new layout is
left as it is; a file with no old tables builds the empty table, so fresh and upgraded files end with the same schema.
"""
from backend import hubdb as H

PRODUCT_PATH = "/api/v2/github/product-repos"
REVIEW_PATH = "/api/v2/github/reviews"

# The table store.py's SCHEMA creates: the same columns, keys and CHECKs (test_migrations compares the two). The state
# CHECK is inline, not a status_values list: that module scans its CHECK tables at every start, and this one is big.
TABLE = """CREATE TABLE idempotency(
 actor TEXT NOT NULL, operation TEXT NOT NULL, key TEXT NOT NULL,
 request_hash TEXT NOT NULL, response TEXT, created_at TEXT NOT NULL, updated_at TEXT,
 binding TEXT CHECK(binding IS NULL OR binding IN ('invites','github_product_repo','github_review')),
 subject TEXT NOT NULL DEFAULT '', state TEXT NOT NULL DEFAULT 'completed' CHECK(state IN ('pending','completed','refused')),
 error_code TEXT, error_detail TEXT, error_status INTEGER,
 PRIMARY KEY(actor,operation,key))"""
# The retention sweep reads the answers by age; a person's removal reads their invite bindings.
INDEXES = (
    "CREATE INDEX IF NOT EXISTS idempotency_answers_by_created ON idempotency(created_at) WHERE binding IS NULL",
    "CREATE INDEX IF NOT EXISTS idempotency_bindings ON idempotency(binding,subject) WHERE binding IS NOT NULL",
)
OLD = ("github_product_repo_operations", "invite_sends", "github_review_operations")


def up(conn):
    names = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    if "binding" in {row[1] for row in conn.execute("PRAGMA table_info(idempotency)")}:
        return
    stamp = H.now()
    answers = "idempotency" in names
    if answers:
        legacy = conn.execute("PRAGMA legacy_alter_table").fetchone()[0]
        conn.execute("PRAGMA legacy_alter_table=ON")
        try:
            conn.execute("ALTER TABLE idempotency RENAME TO idempotency_old")
        finally:
            conn.execute(f"PRAGMA legacy_alter_table={'ON' if legacy else 'OFF'}")
    conn.execute(TABLE)
    if answers:
        # Every answer with its rowid. A product repository's is its binding (the receipt create replayed and bound).
        conn.execute(
            "INSERT INTO idempotency(rowid,actor,operation,key,request_hash,response,created_at,binding,subject) "
            "SELECT rowid,actor,operation,key,request_hash,response,created_at,"
            "CASE WHEN operation=? THEN 'github_product_repo' END,"
            "CASE WHEN operation=? AND json_valid(response) THEN coalesce(json_extract(response,'$.repository'),'') "
            "ELSE '' END FROM idempotency_old", (PRODUCT_PATH, PRODUCT_PATH))
    # The bindings replace an answer under the same key.
    if "github_product_repo_operations" in names:
        conn.execute("INSERT OR REPLACE INTO idempotency(actor,operation,key,request_hash,response,created_at,"
                     "updated_at,binding,subject,state,error_code,error_detail,error_status) "
                     "SELECT actor,operation,key,request_hash,response,created_at,updated_at,'github_product_repo',"
                     "target_org||'/'||target_name,CASE state WHEN 'rejected' THEN 'refused' ELSE state END,"
                     "error_code,error_detail,error_status FROM github_product_repo_operations")
    if "invite_sends" in names:
        conn.execute("INSERT OR REPLACE INTO idempotency(actor,operation,key,request_hash,response,created_at,"
                     "updated_at,binding,subject,state,error_code,error_detail) "
                     "SELECT actor,operation,key,request_hash,NULL,created_at,updated_at,'invites',person,"
                     "CASE state WHEN 'sending' THEN 'pending' WHEN 'sent' THEN 'completed' ELSE 'refused' END,"
                     "error_code,error_detail FROM invite_sends")
    if "github_review_operations" in names:
        # This table has no created_at: its rows are stamped when the migration runs, which only orders them.
        conn.execute("INSERT OR REPLACE INTO idempotency(actor,operation,key,request_hash,response,created_at,"
                     "updated_at,binding,subject,state) "
                     "SELECT actor,?,key,request_hash,response,?,NULL,'github_review','',"
                     "CASE WHEN response IS NULL THEN 'pending' ELSE 'completed' END FROM github_review_operations",
                     (REVIEW_PATH, stamp))
    if answers:
        conn.execute("DROP TABLE idempotency_old")      # with its index and the idempotency_messages triggers
    for old in OLD:
        conn.execute(f"DROP TABLE IF EXISTS {old}")
    for index in INDEXES:
        conn.execute(index)
    if "idempotency_messages" in names:
        # An answer that is now a binding is not indexed (its quotes are never rewritten): drop its entries.
        conn.execute("DELETE FROM idempotency_messages WHERE (actor,operation,key) IN "
                     "(SELECT actor,operation,key FROM idempotency WHERE binding IS NOT NULL)")
