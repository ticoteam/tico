"""The posting lease of a pipeline check run, kept apart from its retry timing (backend/pipeline_github_checks.py).

`retry_at` says when a failed post may be tried again; `lease_until` and `lease_token` say which flush is posting the
row now. A state change resets the retry timing but never the lease, so two flushes cannot post one check twice.
"""


def up(conn):
    columns = {row[1] for row in conn.execute("PRAGMA table_info(pipeline_check_runs)")}
    if not columns:
        return
    if "lease_until" not in columns:
        conn.execute("ALTER TABLE pipeline_check_runs ADD COLUMN lease_until TEXT")
    if "lease_token" not in columns:
        conn.execute("ALTER TABLE pipeline_check_runs ADD COLUMN lease_token TEXT")
