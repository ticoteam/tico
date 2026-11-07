-- Teammates dedicated to a business segment (backend/segments.py): a teammate tagged with a segment id is dedicated to
-- it, and `all` tags one who works across every segment. A segment that stops being one of the company's loses its
-- rows (backend/strategy_market.py), so a segment id here names a segment the company plays in.
CREATE TABLE IF NOT EXISTS segment_members (
  segment_id TEXT NOT NULL,
  member TEXT NOT NULL,
  created_at TEXT NOT NULL,
  created_by TEXT NOT NULL,
  PRIMARY KEY (segment_id, member)
);
CREATE INDEX IF NOT EXISTS segment_members_member ON segment_members(member);
