"""`google_workspaces.authorized_scopes`: the scopes a Workspace key was authorized for in Google Admin.

Google's domain-wide delegation takes only the scopes authorized by name (docs.cloud.google.com/identity/docs/how-to/
setup-policies), so a token is minted only with scopes on this list (backend/google_workspace.py `minting_scopes`). It
is a JSON list, written when a key is stored and again when a check proves a list. A row from before this migration is
null: a key authorized for the first two scopes only (gmail.modify and calendar), which keeps minting those until an
admin redoes step 2 and checks again.
"""


def up(conn):
    if not conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='google_workspaces'").fetchone():
        return
    if "authorized_scopes" not in {row[1] for row in conn.execute("PRAGMA table_info(google_workspaces)")}:
        conn.execute("ALTER TABLE google_workspaces ADD COLUMN authorized_scopes TEXT")
