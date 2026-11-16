"""Connect Google grants move to the server (backend/google_connect.py, backend/google_tokens.py).

The server now trades the sign-in code itself and keeps the refresh token sealed (`grant_sealed`, opened under
`grant_operation_id`). A grant a computer held cannot move: the server only ever kept its fingerprint. So each row
with one (`grant_hash`) ends as `disconnected` with kind `reconnect`, and its person sees "Connect Google again" once.
A disconnect still queued for a computer is done (`disconnected`); a sign-in a computer was finishing ends as
`expired`. A sign-in still open keeps going: its callback now finishes on the server.

The table is made again in one fixed shape (SHAPE, the same as the module's CREATE), whatever columns the file had:
the module's startup code once added some of them itself, so files differ. The columns only the computer handoff used
go (`pending_hash`, `grant_hash`, `pending_scopes`, `holder`, `runner_id`, `code`), with `google_mail_holders`.
`answered` stays in the shape although nothing reads it: 20261112160353_add_operation_to_google_connects adds it when
it runs after this file, so keeping it makes the result the same in either order. The status CHECK comes back at the
next start (backend/status_values.py `enforce`), once the rows hold only the new statuses. A new file has no table
yet: the module's CREATE makes the shape.
"""

GONE = ("pending_hash", "grant_hash", "pending_scopes", "holder", "runner_id", "code")
SHAPE = (
    ("person_id", "TEXT PRIMARY KEY"),
    ("mailbox", "TEXT NOT NULL"),
    ("status", "TEXT NOT NULL"),
    ("oauth_state", "TEXT"),
    ("verifier", "TEXT"),
    ("kind", "TEXT"),
    ("message", "TEXT"),
    ("hint", "TEXT"),
    ("can", "TEXT"),
    ("client_id", "TEXT"),
    ("started_at", "REAL"),
    ("created_at", "TEXT NOT NULL"),
    ("updated_at", "TEXT NOT NULL"),
    ("operation_id", "TEXT"),
    ("answered", "TEXT"),
    ("grant_scopes", "TEXT"),
    ("grant_sealed", "TEXT"),
    ("grant_operation_id", "TEXT"),
)


def up(conn):
    conn.execute("DROP TABLE IF EXISTS google_mail_holders")
    columns = {row[1] for row in conn.execute("PRAGMA table_info(google_connects)")}
    if not columns:
        return
    if [row[1] for row in conn.execute("PRAGMA table_info(google_connects)")] == [name for name, _ in SHAPE]:
        return  # already this shape: a second run changes nothing
    conn.execute(
        "CREATE TABLE google_connects_next(" + ", ".join(f"{name} {declaration}" for name, declaration in SHAPE) + ")"
    )
    kept = [name for name, _ in SHAPE if name in columns]
    conn.execute(f"INSERT INTO google_connects_next({','.join(kept)}) SELECT {','.join(kept)} FROM google_connects")
    conn.execute(
        "UPDATE google_connects_next SET status='disconnected',kind=NULL,message=NULL,hint=NULL "
        "WHERE status IN ('disconnecting','revoking')"
    )
    if "grant_hash" in columns:
        # A grant a computer held: it ends here, and the person connects again once.
        conn.execute(
            "UPDATE google_connects_next SET status='disconnected',kind='reconnect',message=NULL,hint=NULL,can=NULL "
            "WHERE person_id IN (SELECT person_id FROM google_connects WHERE grant_hash IS NOT NULL) "
            "AND status NOT IN ('signing_in','disconnected')"
        )
    conn.execute("DROP TABLE google_connects")
    conn.execute("ALTER TABLE google_connects_next RENAME TO google_connects")
    conn.execute(
        "UPDATE google_connects SET status='failed',kind='expired',message=NULL,hint=NULL,oauth_state=NULL,"
        "verifier=NULL WHERE status IN ('ready','finishing')"
    )
    # Nothing a computer held is used again: the scopes and client of those grants go with them.
    conn.execute("UPDATE google_connects SET grant_scopes=NULL,client_id=NULL WHERE grant_sealed IS NULL")
    conn.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS google_connects_oauth_state ON google_connects(oauth_state) "
        "WHERE oauth_state IS NOT NULL"
    )
