"""A strategy bet's optional segment (backend/strategy.py): the business segment it is about, or none. On a new file the
strategy module's table is made after the migrations, with the column already in it (strategy.SCHEMA), so only a table
that exists gets the column here."""


def up(conn):
    if not conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='strategy_bets'").fetchone():
        return
    if "segment_id" not in {row[1] for row in conn.execute("PRAGMA table_info(strategy_bets)")}:
        conn.execute("ALTER TABLE strategy_bets ADD COLUMN segment_id TEXT")
