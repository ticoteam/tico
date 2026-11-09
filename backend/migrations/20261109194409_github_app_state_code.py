"""The code GitHub returns for the App's setup, parked on the setup's state until the owner's agent finishes it
(backend/github_app.py `finish`), and when it came back. A return with no owner session parks its code here instead of
exchanging it. On a new file the module code makes the table after the migrations, with both columns already in it, so
only a table that exists gets them here."""


def up(conn):
    if not conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='github_app_states'").fetchone():
        return
    have = {row[1] for row in conn.execute("PRAGMA table_info(github_app_states)")}
    if "code" not in have:
        conn.execute("ALTER TABLE github_app_states ADD COLUMN code TEXT")
    if "returned_at" not in have:
        conn.execute("ALTER TABLE github_app_states ADD COLUMN returned_at REAL")
