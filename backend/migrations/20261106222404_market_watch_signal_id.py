"""The Gateway's Signals id of each page watch (backend/market_watch.py `mirror`): the watch HQ reads for this install.
On a new file the module code makes the table after the migrations, with the column already in it, so only a table that
exists gets the column here."""


def up(conn):
    if not conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='market_watches'").fetchone():
        return
    if "signal_watch_id" not in {row[1] for row in conn.execute("PRAGMA table_info(market_watches)")}:
        conn.execute("ALTER TABLE market_watches ADD COLUMN signal_watch_id TEXT")
