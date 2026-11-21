-- When a computer's reported update last moved (its state, target or drain counts changed), apart from when its hold on
-- new work began (update_since): Health's update_stuck finding tells a switch from waiting to updating from a hang.
ALTER TABLE runner_versions ADD COLUMN update_changed_at TEXT;
