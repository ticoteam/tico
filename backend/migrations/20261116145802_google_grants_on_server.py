"""Connect Google grants move to the server (backend/google_connect.py, backend/google_tokens.py).

The server now trades the sign-in code itself and keeps the refresh token sealed (`grant_sealed`, opened under
`grant_operation_id`). A grant a computer held cannot move: the server only ever kept its fingerprint. So each row
with one (`grant_hash`) ends as `disconnected` with kind `reconnect`, and its person sees "Connect Google again" once.
A disconnect still queued for a computer is done (`disconnected`); a sign-in a computer was finishing ends as
`expired`. A sign-in still open keeps going: its callback now finishes on the server.

The columns only the computer handoff used go (`pending_hash`, `grant_hash`, `pending_scopes`, `holder`,
`runner_id`, `code`, `answered`), with `google_mail_holders`. A new file has no table yet: the module's CREATE makes
the new shape.
"""

GONE = ("pending_hash", "grant_hash", "pending_scopes", "holder", "runner_id", "code", "answered")


def up(conn):
    conn.execute("DROP TABLE IF EXISTS google_mail_holders")
    columns = {row[1] for row in conn.execute("PRAGMA table_info(google_connects)")}
    if not columns:
        return
    for column in ("grant_sealed", "grant_operation_id"):
        if column not in columns:
            conn.execute(f"ALTER TABLE google_connects ADD COLUMN {column} TEXT")
    conn.execute(
        "UPDATE google_connects SET status='disconnected',kind=NULL,message=NULL,hint=NULL "
        "WHERE status IN ('disconnecting','revoking')"
    )
    if "grant_hash" in columns:
        conn.execute(
            "UPDATE google_connects SET status='disconnected',kind='reconnect',message=NULL,hint=NULL,can=NULL "
            "WHERE grant_hash IS NOT NULL AND status NOT IN ('signing_in','disconnected')"
        )
    conn.execute(
        "UPDATE google_connects SET status='failed',kind='expired',message=NULL,hint=NULL,oauth_state=NULL,"
        "verifier=NULL WHERE status IN ('ready','finishing')"
    )
    # Nothing a computer held is used again: the scopes and client of those grants go with them.
    conn.execute("UPDATE google_connects SET grant_scopes=NULL,client_id=NULL WHERE grant_sealed IS NULL")
    for column in GONE:
        if column in columns:
            conn.execute(f"ALTER TABLE google_connects DROP COLUMN {column}")
