"""When a mailbox's watch started (`mail_mailboxes.watching_since_at`, backend/automations.py `mail_watch`): the mail
its message automations act on is what arrived from there on, so a first sync's older days and a reconnect's gap are
not replayed. A mailbox from before this has none, and its watch is read from its last sync until its next sync keeps
one. On a new file the table does not exist yet: store.py SCHEMA makes it with the column."""


def _columns(conn, table):
    return {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}


def up(conn):
    if not conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='mail_mailboxes'").fetchone():
        return
    if "watching_since_at" not in _columns(conn, "mail_mailboxes"):
        conn.execute("ALTER TABLE mail_mailboxes ADD COLUMN watching_since_at TEXT")
