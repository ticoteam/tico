"""What new commits after a person's OK are, for the person asked again (task #435, criterion 4).

- `github_pr_diffs.commits`: JSON [[sha, title]], the PR's own commits up to that commit, oldest first, as GitHub's
  compare of the base branch with it lists them. NULL for a commit read before this, or when GitHub gave none.
- `github_pr_diffs.files`: JSON {file name: id of that file's part of the diff} (item_sources.github.diff_of). NULL for
  a commit read before this, or when GitHub could not give the whole diff.

item_sources.github.since compares two commits' rows: how many commits are new and how many files' changes differ.
"""


def up(conn):
    # Pending migrations run in any order: when this one runs before the one that makes the table, it makes the whole
    # table here (that one's CREATE TABLE IF NOT EXISTS then leaves it), so the schema is the same either way.
    conn.execute(
        "CREATE TABLE IF NOT EXISTS github_pr_diffs ("
        "repo TEXT NOT NULL, "
        "sha TEXT NOT NULL, "
        "diff_id TEXT, "
        "read_at TEXT NOT NULL, "
        "commits TEXT, "
        "files TEXT, "
        "PRIMARY KEY (repo, sha))"
    )
    columns = {r[1] for r in conn.execute("PRAGMA table_info(github_pr_diffs)")}
    for column in ("commits", "files"):
        if column not in columns:
            conn.execute(f"ALTER TABLE github_pr_diffs ADD COLUMN {column} TEXT")
