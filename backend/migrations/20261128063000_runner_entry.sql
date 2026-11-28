-- Which package started each computer's runner, as its last release report said (runner-rs/src/follow.rs entry):
-- 'runner' (the legacy runner/ package), 'runner_support', or '' when it did not say. runner/ is deleted only once no
-- computer reports 'runner' and none is unreported (backend/runner_versions.py starts).
ALTER TABLE runner_versions ADD COLUMN entry TEXT NOT NULL DEFAULT '';
