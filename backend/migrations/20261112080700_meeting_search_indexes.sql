-- Meeting search (backend/context_search.py candidates) reads meetings newest first and stops once it has a page,
-- and finds the meetings in a date range without parsing every meeting's metadata. The day expression is the one
-- context_search.RECORDED_DAY queries with, character for character: SQLite uses an expression index only for the
-- same expression.
CREATE INDEX IF NOT EXISTS meetings_newest ON meetings(created_at DESC, id);
CREATE INDEX IF NOT EXISTS meetings_recorded_day ON meetings((CASE WHEN json_type(metadata,'$.started')='text' AND json_extract(metadata,'$.started')<>'' THEN substr(json_extract(metadata,'$.started'),1,10) WHEN (json_type(metadata,'$.started') IS NULL OR json_type(metadata,'$.started') IN ('null','false') OR json_type(metadata,'$.started')='text') AND typeof(created_at)='text' THEN substr(created_at,1,10) END));
