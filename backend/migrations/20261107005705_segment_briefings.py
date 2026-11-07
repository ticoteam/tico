"""A Market briefing is the week's for the whole market (segment_id '') or for one segment (backend/market_briefing.py).
The table keeps one row per week and segment, so its week is no longer unique on its own: a table rebuild, the rows kept
and their segment '' (the whole market, as before). A table that does not exist yet is made by the module code after the
migrations, with the column and the new unique key already in it, so there is nothing to change then.
"""

FOREIGN_KEYS_OFF = True

# The columns the module's schema declares (backend/market_briefing.py SCHEMA), with source and answers added after
# the first version, so an older table may lack either: the rows copy whichever columns both tables have.
COLUMNS = (
    "id",
    "week",
    "segment_id",
    "title",
    "body",
    "items",
    "bets",
    "created_by",
    "created_at",
    "updated_at",
    "answers",
    "source",
)
CREATE = (
    "CREATE TABLE market_briefings_new (id TEXT PRIMARY KEY, week TEXT NOT NULL, segment_id TEXT NOT NULL DEFAULT '', "
    "title TEXT NOT NULL, body TEXT NOT NULL, items TEXT NOT NULL DEFAULT '[]', bets TEXT NOT NULL DEFAULT '[]', "
    "created_by TEXT, created_at TEXT, updated_at TEXT, answers TEXT, source TEXT, UNIQUE(week, segment_id))"
)


def up(conn):
    if not conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='market_briefings'").fetchone():
        return
    old = [row[1] for row in conn.execute("PRAGMA table_info(market_briefings)")]
    if "segment_id" in old:
        return
    # Triggers and indexes on the old table are kept: they are recreated on the new one after the rename.
    objects = [
        row[0]
        for row in conn.execute(
            "SELECT sql FROM sqlite_master WHERE tbl_name='market_briefings' AND type IN ('index','trigger') "
            "AND sql IS NOT NULL"
        )
    ]
    conn.execute("DROP TABLE IF EXISTS market_briefings_new")
    conn.execute(CREATE)
    common = ",".join(f'"{name}"' for name in COLUMNS if name in old)
    conn.execute(f"INSERT INTO market_briefings_new ({common}) SELECT {common} FROM market_briefings")
    conn.execute("DROP TABLE market_briefings")
    legacy = conn.execute("PRAGMA legacy_alter_table").fetchone()[0]
    conn.execute("PRAGMA legacy_alter_table=ON")
    try:
        conn.execute("ALTER TABLE market_briefings_new RENAME TO market_briefings")
    finally:
        conn.execute(f"PRAGMA legacy_alter_table={'ON' if legacy else 'OFF'}")
    for statement in objects:
        conn.execute(statement)
