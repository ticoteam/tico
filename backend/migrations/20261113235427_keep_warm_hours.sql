-- Keep active tasks warm (backend/keep_warm.py): how many hours after its last real work a task session in an active
-- stage is kept in the prompt cache by a one-line turn. On the pipeline (default 8, 0 = off), and for one stage in
-- `stage_keep_warm` (no row: the pipeline's). Its own table, not a stages column: a column added to stages by two
-- pending migrations would read differently by the order they ran in.
ALTER TABLE pipelines ADD COLUMN keep_warm_hours INTEGER NOT NULL DEFAULT 8 CHECK (keep_warm_hours BETWEEN 0 AND 24);
CREATE TABLE IF NOT EXISTS stage_keep_warm (
 stage_id TEXT PRIMARY KEY REFERENCES stages(id),
 hours INTEGER NOT NULL CHECK (hours BETWEEN 0 AND 24));
