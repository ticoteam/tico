"""One table for the stored retry answers and the retained operation bindings (backend/store.py's `idempotency`).

Before this file, the answers (swept after 48 hours) lived in `idempotency`, and three tables kept the operation bindings
that are never swept: `github_product_repo_operations` (backend/github_app.py), `invite_sends` (backend/invites.py) and
`github_review_operations` (backend/github_reviews.py). Their rows move into `idempotency`:

- an answer keeps binding NULL and state completed;
- a binding sets `binding` to the operation's name, `subject` to what it is about (an invite's person id, a product
  repository's org/name, empty for a review) and `state` to pending, completed or refused (sending and sent become
  pending and completed, rejected becomes refused, a review with no response is pending);
- a product repository's answer that was only in `idempotency` (the earlier implementation's receipt, which create
  copied into a binding on retry) becomes its binding, so the create is still never repeated.

Where a binding and an answer share a key (the invite routes once stored both under one key), the binding wins: it
carries the send's state, and the answer it replaced was a replay of a record the binding already makes.

The old answer table is renamed aside, its rows are copied with their rowids (the answer triggers and the
`idempotency_messages` index refer to rows by key, so they stay valid), and it is dropped with its indexes and triggers.
Every statement runs in the migration's one transaction, so a failure leaves the old layout as it was. A file with no
old tables builds the empty table, so fresh and upgraded files end with the same schema.
"""
from backend import hubdb as H
from backend import idempotency_messages

PRODUCT_PATH = "/api/v2/github/product-repos"
REVIEW_PATH = "/api/v2/github/reviews"

# The table store.py's SCHEMA creates: the same columns, keys and CHECKs (test_migrations compares the two).
TABLE = """CREATE TABLE idempotency(
 actor TEXT NOT NULL, operation TEXT NOT NULL, key TEXT NOT NULL,
 request_hash TEXT NOT NULL, response TEXT, created_at TEXT NOT NULL, updated_at TEXT,
 binding TEXT CHECK(binding IS NULL OR binding IN ('invites','github_product_repo','github_review')),
 subject TEXT NOT NULL DEFAULT '', state TEXT NOT NULL DEFAULT 'completed',
 error_code TEXT, error_detail TEXT, error_status INTEGER,
 PRIMARY KEY(actor,operation,key),
 CONSTRAINT status_values_state CHECK (state IN ('pending', 'completed', 'refused')))"""


def up(conn):
    names = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    layout = {row[1] for row in conn.execute("PRAGMA table_info(idempotency)")} if "idempotency" in names else set()
    # A file that already has the new layout (the migration ran, and its version was cleared to run it again) is only
    # checked: the rebuild would otherwise read the bindings back as answers.
    if "binding" not in layout:
        _build(conn, names)
    idempotency_messages.ensure(conn)
    # An answer that is now a binding is no longer indexed (its trigger never fires for it): drop its old entries.
    conn.execute("DELETE FROM idempotency_messages WHERE (actor,operation,key) IN "
                 "(SELECT actor,operation,key FROM idempotency WHERE binding IS NOT NULL)")


def _build(conn, names):
    stamp = H.now()
    answers = "idempotency" in names
    if answers:
        conn.execute("ALTER TABLE idempotency RENAME TO idempotency_old")
    conn.execute(TABLE)
    if answers:
        # Answers first, with their rowids. A product repository's answer is copied below, unless a binding exists.
        conn.execute("INSERT INTO idempotency(rowid,actor,operation,key,request_hash,response,created_at) "
                     "SELECT rowid,actor,operation,key,request_hash,response,created_at FROM idempotency_old "
                     "WHERE operation<>?", (PRODUCT_PATH,))
    if "github_product_repo_operations" in names:
        conn.execute("INSERT OR REPLACE INTO idempotency(actor,operation,key,request_hash,response,created_at,"
                     "updated_at,binding,subject,state,error_code,error_detail,error_status) "
                     "SELECT actor,operation,key,request_hash,response,created_at,updated_at,'github_product_repo',"
                     "target_org||'/'||target_name,CASE state WHEN 'rejected' THEN 'refused' ELSE state END,"
                     "error_code,error_detail,error_status FROM github_product_repo_operations")
    if answers:
        # The earlier implementation's receipt for a product repository: create replayed it, and then bound it.
        conn.execute("INSERT OR IGNORE INTO idempotency(actor,operation,key,request_hash,response,created_at,"
                     "binding,subject,state) "
                     "SELECT actor,operation,key,request_hash,response,created_at,'github_product_repo',"
                     "CASE WHEN json_valid(response) THEN coalesce(json_extract(response,'$.repository'),'') ELSE '' END,"
                     "'completed' FROM idempotency_old WHERE operation=?", (PRODUCT_PATH,))
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
        conn.execute("DROP TABLE idempotency_old")
    for old in ("github_product_repo_operations", "invite_sends", "github_review_operations"):
        conn.execute(f"DROP TABLE IF EXISTS {old}")
    conn.execute("CREATE INDEX IF NOT EXISTS idempotency_by_created ON idempotency(created_at)")
    conn.execute("CREATE INDEX IF NOT EXISTS idempotency_answers_by_created ON idempotency(created_at) "
                 "WHERE binding IS NULL")
