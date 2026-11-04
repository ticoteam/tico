"""Settings > Google, the Workspace connection on this server (backend/google_workspace.py).

`google_workspaces` holds the install's service-account key for its Workspace: the active row (`connected`, or `failing`
after a check or token mints have failed for a while) and at most one `pending` replacement. The key is sealed
(vault.cipher, context `google-workspace:<id>`). `google_setup_codes` keeps the hash of each setup command's code, its
generation (only the newest generation may upload a key) and when it was used. `google_oauth_clients` is the OAuth
client an admin pasted in when the server's environment has none; its secret is sealed. `connector_reports` is each
computer's health per service and account, so one computer's failure does not hide another's.

`google_connects.client_id` is the OAuth client a grant was made with. A row from before this migration is null, which
means unknown: it is reconnected only when a refresh fails. On a new file the table does not exist yet, so its
module's CREATE carries the column instead (backend/google_connect.py).
"""

GOOGLE_WORKSPACES = (
    "CREATE TABLE IF NOT EXISTS google_workspaces(id TEXT PRIMARY KEY, domain TEXT NOT NULL, project_id TEXT NOT NULL "
    "DEFAULT '', client_email TEXT NOT NULL, client_id TEXT NOT NULL, key_id TEXT NOT NULL, key_sealed TEXT NOT NULL, "
    "revision INTEGER NOT NULL DEFAULT 1, status TEXT NOT NULL, checked_at TEXT, connected_at TEXT, last_error TEXT, "
    "probe_mailbox TEXT, created_by TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL, replaces_id TEXT, "
    "generation INTEGER NOT NULL DEFAULT 0, mint_failing_since TEXT)"
)
GOOGLE_WORKSPACES_PENDING = (
    "CREATE UNIQUE INDEX IF NOT EXISTS google_workspaces_pending ON google_workspaces(status) WHERE status='pending'"
)
GOOGLE_SETUP_CODES = (
    "CREATE TABLE IF NOT EXISTS google_setup_codes(code_hash TEXT PRIMARY KEY, domain TEXT NOT NULL, created_by TEXT NOT "
    "NULL, created_at TEXT NOT NULL, expires_at TEXT NOT NULL, used_at TEXT, generation INTEGER NOT NULL)"
)
GOOGLE_OAUTH_CLIENTS = (
    "CREATE TABLE IF NOT EXISTS google_oauth_clients(singleton INTEGER PRIMARY KEY CHECK (singleton = 1), client_id TEXT "
    "NOT NULL, secret_sealed TEXT NOT NULL, revision INTEGER NOT NULL, updated_by TEXT NOT NULL, updated_at TEXT NOT NULL)"
)
CONNECTOR_REPORTS = (
    "CREATE TABLE IF NOT EXISTS connector_reports(service TEXT NOT NULL, runner_id TEXT NOT NULL, account TEXT NOT NULL "
    "DEFAULT '', error TEXT NOT NULL DEFAULT '', failing_json TEXT NOT NULL DEFAULT '[]', ok_accounts_json TEXT NOT NULL "
    "DEFAULT '[]', reported_at TEXT NOT NULL, PRIMARY KEY(service, runner_id, account))"
)


def up(conn):
    for statement in (GOOGLE_WORKSPACES, GOOGLE_WORKSPACES_PENDING, GOOGLE_SETUP_CODES, GOOGLE_OAUTH_CLIENTS,
                      CONNECTOR_REPORTS):
        conn.execute(statement)
    if conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='google_connects'").fetchone():
        if "client_id" not in {row[1] for row in conn.execute("PRAGMA table_info(google_connects)")}:
            conn.execute("ALTER TABLE google_connects ADD COLUMN client_id TEXT")
