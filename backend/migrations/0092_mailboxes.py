"""`mailboxes` and `mailbox_accesses`: which inboxes Tico uses and who may use each (backend/mailbox_access.py).

`mailboxes` is one row per address (lowercase, unique): `kind` personal (its person in `person_id`, matched by their
verified email; NULL while it awaits them) or shared (no person, an admin made it shared), `connection` workspace (the
company's Google Workspace key) or personal (the person's own Connect Google grant), `status` active or stopped.
`mailbox_accesses` is one explicit grant per (mailbox, person or bot): `level` read or read_write. `mail_mailboxes`
stays the sync state.

The backfill keeps every access people and bots have today, written as rows, so the upgrade changes nobody's mail
except a manager's automatic view of their reports' mail, which is dropped. It writes only what is missing, so a second
run writes nothing. On a new database the source tables do not exist yet and nothing is written.
"""


def up(conn):
    conn.execute("CREATE TABLE IF NOT EXISTS mailboxes(id TEXT PRIMARY KEY, address TEXT NOT NULL UNIQUE, "
                 "provider TEXT NOT NULL DEFAULT 'google_workspace', person_id TEXT, "
                 "kind TEXT NOT NULL DEFAULT 'personal' CHECK(kind IN ('personal','shared')), "
                 "connection TEXT NOT NULL DEFAULT 'workspace' CHECK(connection IN ('workspace','personal')), "
                 "status TEXT NOT NULL DEFAULT 'active' CHECK(status IN ('active','stopped')), "
                 "shared_by TEXT, shared_at TEXT, created_by TEXT NOT NULL, created_at TEXT NOT NULL, "
                 "updated_at TEXT NOT NULL, CHECK(address = lower(address)), "
                 "CHECK(kind = 'personal' OR person_id IS NULL))")
    conn.execute("CREATE INDEX IF NOT EXISTS mailboxes_person ON mailboxes(person_id) WHERE person_id IS NOT NULL")
    conn.execute("CREATE TABLE IF NOT EXISTS mailbox_accesses(id TEXT PRIMARY KEY, "
                 "mailbox_id TEXT NOT NULL REFERENCES mailboxes(id) ON DELETE CASCADE, "
                 "grantee_type TEXT NOT NULL CHECK(grantee_type IN ('person','bot')), grantee_id TEXT NOT NULL, "
                 "level TEXT NOT NULL CHECK(level IN ('read','read_write')), granted_by TEXT NOT NULL, "
                 "created_at TEXT NOT NULL, updated_at TEXT NOT NULL)")
    conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS mailbox_accesses_grantee "
                 "ON mailbox_accesses(mailbox_id, grantee_type, grantee_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS mailbox_accesses_of ON mailbox_accesses(grantee_type, grantee_id)")
    from backend import mailbox_access
    mailbox_access.backfill(conn)
