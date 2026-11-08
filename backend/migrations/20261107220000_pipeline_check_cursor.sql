-- Pipeline Track C: where the scheduler's check pass resumes (backend/pipeline_github_checks.py). One row, id 1, holding the
-- last ticket id a pass recorded; a short pass stores '' so the next one starts from the first ticket again. The lease
-- columns make one pass run at a time (a pass holds the lease until lease_until, or until it releases it), and the failure
-- columns count the passes that failed on the same next ticket. It lives in the database, so a restart or a second process
-- resumes where the last pass stopped. No ticket data is kept here.
CREATE TABLE IF NOT EXISTS pipeline_check_cursor (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    task_id TEXT NOT NULL DEFAULT '',
    lease_until TEXT,
    lease_token TEXT,
    failed_task_id TEXT NOT NULL DEFAULT '',
    failed_passes INTEGER NOT NULL DEFAULT 0
);
