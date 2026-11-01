"""Growth segments (backend/growth.py): who the company sells to, each a market `segment` entity with its growth fields.
Serving mirrors `company/self` `operates_in` the segment and next its `next: true` property (backend/strategy_market.py
keeps both in step). Channels and ideas name the segments they reach; a cohort may be one segment's share of a channel's
month, so the one-row rule takes the segment in. Segments the graph already marks Us or Next start as serving or next.

A file from a release that ran this as hubdb's step 72 (migrations.NEWEST_USER_VERSION) already has all of it, so each
step is guarded and the graph is read only when the table is new: a person's later changes stay."""


def up(conn):
    seed = not conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='growth_segments'").fetchone()
    conn.execute("""CREATE TABLE IF NOT EXISTS growth_segments (
  id TEXT PRIMARY KEY, segment_id TEXT NOT NULL UNIQUE,
  status TEXT NOT NULL CHECK(status IN ('serving','testing','next','dropped')),
  motion TEXT CHECK(motion IS NULL OR motion IN ('self_serve','sales_led','partner','marketplace','local','community',
                                                 'ecommerce')),
  who_buys TEXT, deal_size TEXT, why TEXT NOT NULL DEFAULT '', owner_ref TEXT, sort INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL, updated_at TEXT NOT NULL, updated_by TEXT)""")
    conn.execute("""CREATE TABLE IF NOT EXISTS growth_channel_segments (
  channel_id TEXT NOT NULL, segment_row_id TEXT NOT NULL, PRIMARY KEY(channel_id, segment_row_id))""")
    conn.execute("CREATE INDEX IF NOT EXISTS growth_channel_segments_segment ON growth_channel_segments(segment_row_id)")
    conn.execute("""CREATE TABLE IF NOT EXISTS growth_idea_segments (
  task_id TEXT NOT NULL, segment_row_id TEXT NOT NULL, PRIMARY KEY(task_id, segment_row_id))""")
    conn.execute("CREATE INDEX IF NOT EXISTS growth_idea_segments_segment ON growth_idea_segments(segment_row_id)")
    if "segment_id" not in {row[1] for row in conn.execute("PRAGMA table_info(growth_cohorts)")}:
        conn.execute("ALTER TABLE growth_cohorts ADD COLUMN segment_id TEXT")
    conn.execute("DROP INDEX IF EXISTS growth_cohorts_month")
    conn.execute("""CREATE UNIQUE INDEX IF NOT EXISTS growth_cohorts_month_segment
  ON growth_cohorts(coalesce(channel_id, ''), month, coalesce(segment_id, ''))""")
    if seed:
        # An upgrading file still has since and until here (0031 renames them); a new one was made with the new names.
        edges = {row[1] for row in conn.execute("PRAGMA table_info(market_edges)")}
        since, until = ("since", "until") if "since" in edges else ("valid_from_on", "valid_until_on")
        current = (f"(m.{since} IS NULL OR substr(m.{since},1,10)<=date('now')) "
                   f"AND (m.{until} IS NULL OR substr(m.{until},1,10)>=date('now'))")
        conn.execute(f"""INSERT OR IGNORE INTO growth_segments (id, segment_id, status, created_at, updated_at)
  SELECT lower(hex(randomblob(16))), e.id,
    CASE WHEN EXISTS (SELECT 1 FROM market_edges m WHERE m.rel='operates_in' AND m.src='company/self' AND m.dst=e.id
      AND {current})
    THEN 'serving' ELSE 'next' END,
    strftime('%Y-%m-%dT%H:%M:%fZ','now'), strftime('%Y-%m-%dT%H:%M:%fZ','now')
  FROM market_entities e WHERE e.type='segment' AND e.status='active' AND (
    EXISTS (SELECT 1 FROM market_edges m WHERE m.rel='operates_in' AND m.src='company/self' AND m.dst=e.id
      AND {current})
    OR (json_valid(e.properties) AND json_extract(e.properties,'$.next')))""")
