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
carries the send's state, and the answer was a replay of the record the binding already makes. An access invite's
binding keeps that answer's recorded time as `response` {"invited_at"}. An invite route's answer with no binding (an
onboarding roster add committed before its invite was bound; nothing was sent) is removed, so its retry binds and sends.

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
# The invite routes (backend/app.py), as LIKE patterns.
ACCESS_INVITE = "/api/v2/access/people/%/invite"
ONBOARDING_INVITE = "/api/v2/onboarding/company/people/%/invite"

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
        # An access invite's answer under the same key was the time the send was recorded (a JSON string): the binding
        # keeps it as {"invited_at"}, so a repeat answers that time (invites.record_send). Nothing else is kept.
        if answers:
            invited_at = ("CASE WHEN s.operation LIKE ? AND json_valid(a.response) THEN CASE json_type(a.response) "
                          "WHEN 'text' THEN json_object('invited_at',json_extract(a.response,'$')) "
                          "WHEN 'object' THEN CASE WHEN json_type(a.response,'$.invited_at')='text' "
                          "THEN json_object('invited_at',json_extract(a.response,'$.invited_at')) END END END")
            source = ("invite_sends s LEFT JOIN idempotency_old a "
                      "ON a.actor=s.actor AND a.operation=s.operation AND a.key=s.key")
            params = (ACCESS_INVITE,)
        else:
            invited_at, source, params = "NULL", "invite_sends s", ()
        conn.execute("INSERT OR REPLACE INTO idempotency(actor,operation,key,request_hash,response,created_at,"
                     "updated_at,binding,subject,state,error_code,error_detail) "
                     f"SELECT s.actor,s.operation,s.key,s.request_hash,{invited_at},s.created_at,s.updated_at,"
                     "'invites',s.person,"
                     "CASE s.state WHEN 'sending' THEN 'pending' WHEN 'sent' THEN 'completed' ELSE 'refused' END,"
                     f"s.error_code,s.error_detail FROM {source}", params)
    # An invite route's answer with no binding is a roster add an older onboarding route committed before it bound the
    # invite (a crash between the two, or a refusal before the issuer was asked). Nothing was sent for it: the binding
    # is always written before the issuer is asked. Kept, it would stand in the way of the binding a retry writes.
    conn.execute("DELETE FROM idempotency WHERE binding IS NULL AND (operation LIKE ? OR operation LIKE ?)",
                 (ACCESS_INVITE, ONBOARDING_INVITE))
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
        # An answer that is now a binding is not indexed (its quotes are never rewritten), nor one that went: drop
        # their entries. No answer is left on an invite route.
        conn.execute("DELETE FROM idempotency_messages WHERE (actor,operation,key) IN "
                     "(SELECT actor,operation,key FROM idempotency WHERE binding IS NOT NULL) "
                     "OR operation LIKE ? OR operation LIKE ?", (ACCESS_INVITE, ONBOARDING_INVITE))
