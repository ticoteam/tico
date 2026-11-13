"""Which saved releases hold a commit, in one read: `github_release_commits` by `commit_sha`.

In a release, the release re-read and a release cut ask it of every saved release of a repository at once
(backend/item_sources/github.py `holds`) instead of one release at a time, so the read no longer grows with releases.
"""


def up(conn):
    if conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='github_release_commits'").fetchone():
        conn.execute(
            "CREATE INDEX IF NOT EXISTS github_release_commits_sha ON github_release_commits(commit_sha, release_id, included)"
        )
