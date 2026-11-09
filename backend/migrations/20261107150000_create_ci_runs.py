"""CI build-time caps (backend/ci_runs.py): every workflow run of the repository, and each builder's remote run the permit
covered, with its job minutes and cost; and a ticket's one extension of its minutes cap.

A run is `kind` (ci, pr, queue, release, remote, other, or builder for a permitted remote run), `status` (permitted,
refused, recorded or unpermitted). `minutes` and `cost_cents` are known once the run's jobs have finished (`seen_at`
set); `entry_id` is the Finance accrual that posted its cost, empty until it does.
"""


def up(conn):
    conn.execute("""CREATE TABLE IF NOT EXISTS ci_runs(
        id INTEGER PRIMARY KEY, kind TEXT NOT NULL, status TEXT NOT NULL,
        remote_id TEXT NOT NULL DEFAULT '', github_run_id TEXT NOT NULL DEFAULT '',
        task_id TEXT NOT NULL DEFAULT '', bot TEXT NOT NULL DEFAULT '',
        cpus INTEGER NOT NULL DEFAULT 4, requested_minutes INTEGER NOT NULL DEFAULT 0,
        minutes INTEGER NOT NULL DEFAULT 0, reserve_cents INTEGER NOT NULL DEFAULT 0,
        cost_cents INTEGER NOT NULL DEFAULT 0, hold_id TEXT NOT NULL DEFAULT '',
        entry_id TEXT NOT NULL DEFAULT '', reason TEXT NOT NULL DEFAULT '',
        created_at TEXT NOT NULL, seen_at TEXT NOT NULL DEFAULT '')""")
    conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS ci_runs_remote ON ci_runs(remote_id) WHERE remote_id != ''")
    conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS ci_runs_github ON ci_runs(github_run_id) WHERE github_run_id != ''")
    conn.execute("CREATE INDEX IF NOT EXISTS ci_runs_task ON ci_runs(task_id, status, created_at)")
    conn.execute(
        "CREATE INDEX IF NOT EXISTS ci_runs_unposted ON ci_runs(seen_at) WHERE entry_id = '' AND cost_cents > 0"
    )
    conn.execute("""CREATE TABLE IF NOT EXISTS ci_run_extensions(
        id INTEGER PRIMARY KEY, task_id TEXT NOT NULL UNIQUE, minutes INTEGER NOT NULL,
        granted_by TEXT NOT NULL, created_at TEXT NOT NULL)""")
