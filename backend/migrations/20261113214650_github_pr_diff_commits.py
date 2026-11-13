"""What new commits after a person's OK are, for the person asked again (task #435, criterion 4).

- `github_pr_diffs.commits`: JSON [[sha, title]], the PR's own commits up to that commit, oldest first, as GitHub's
  compare of the base branch with it lists them. NULL for a commit read before this, or when GitHub gave none.
- `github_pr_diffs.files`: JSON {file name: id of that file's part of the diff} (item_sources.github.diff_of). NULL for
  a commit read before this, or when GitHub could not give the whole diff.

item_sources.github.since compares two commits' rows: how many commits are new and how many files' changes differ.
"""


def up(conn):
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    if "github_pr_diffs" not in tables:
        return
    columns = {r[1] for r in conn.execute("PRAGMA table_info(github_pr_diffs)")}
    for column in ("commits", "files"):
        if column not in columns:
            conn.execute(f"ALTER TABLE github_pr_diffs ADD COLUMN {column} TEXT")
