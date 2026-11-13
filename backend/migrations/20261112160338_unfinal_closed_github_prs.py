"""A closed, unmerged GitHub PR is no longer final (SM-09): it can be reopened, and a final row is never read again, so
a delayed `closed` webhook that tied with a newer `reopened` left the PR closed for good. Only a merged PR stays final.
This clears `final_at` on the closed PRs the old code marked final, so the next pass reads the ones a task still waits
on (item_metadata.due_reads). Running it again changes nothing.
"""


def up(conn):
    if not conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='item_metadata'").fetchone():
        return
    conn.execute(
        "UPDATE item_metadata SET final_at=NULL WHERE source='github' AND item_type='pr' AND final_at IS NOT NULL "
        "AND json_valid(fields) AND COALESCE(json_extract(fields, '$.state'), '') <> 'merged'"
    )
