"""The Librarian's one-line answers to the Market page's five questions (backend/market_briefing.py `answers`): a JSON
object keyed changed, money, players, reach and saying. On a new file the module code makes the table after the
migrations, with the column already in it, so there is nothing to add here then."""


def up(conn):
    if not conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='market_briefings'").fetchone():
        return
    if "answers" not in {row[1] for row in conn.execute("PRAGMA table_info(market_briefings)")}:
        conn.execute("ALTER TABLE market_briefings ADD COLUMN answers TEXT")
