-- Which package started each computer's runner, as its last release report said (runner-rs/src/follow.rs entry):
-- 'runner' (the legacy runner/ package) or 'runner_support'. No row, or a report without it, is not reported. runner/
-- is deleted only once no computer reports 'runner' and none is unreported (backend/runner_versions.py starts). Its own
-- table, not a runner_versions column: two pending migrations adding columns to one table give a column order that
-- depends on which runs first.
CREATE TABLE IF NOT EXISTS runner_entries(
 runner_id TEXT PRIMARY KEY REFERENCES runners(id) ON DELETE CASCADE,
 entry TEXT NOT NULL CHECK(entry IN ('runner','runner_support')),
 reported_at TEXT NOT NULL
);
