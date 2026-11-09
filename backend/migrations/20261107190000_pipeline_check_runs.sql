-- Pipeline Track C: the GitHub check run a requirement publishes on a pull request's commit. One row per
-- (repository, commit, requirement): `state` is what the check should say now, `posted_state` what GitHub was last told,
-- so a check is posted once per state and a failed post is retried (backend/pipeline_github_checks.py). No foreign key
-- to tasks, so a purge keeps working (task_delete.py lists the table).
CREATE TABLE IF NOT EXISTS pipeline_check_runs (
    id TEXT PRIMARY KEY,
    task_id TEXT NOT NULL,
    stage_requirement_id TEXT NOT NULL,
    repo TEXT NOT NULL,
    head_sha TEXT NOT NULL,
    state TEXT NOT NULL CHECK (state IN ('met','failed','waiting')),
    posted_state TEXT CHECK (posted_state IS NULL OR posted_state IN ('met','failed','waiting')),
    check_run_id INTEGER,
    attempts INTEGER NOT NULL DEFAULT 0,
    retry_at TEXT,
    last_error TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    posted_at TEXT
);
CREATE UNIQUE INDEX IF NOT EXISTS pipeline_check_runs_commit ON pipeline_check_runs(repo, head_sha, stage_requirement_id);
CREATE INDEX IF NOT EXISTS pipeline_check_runs_task ON pipeline_check_runs(task_id);
