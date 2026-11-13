"""What each PR commit changes, as one id (docs/pipelines-v3.md C22).

- `github_pr_diffs`: per repo and commit, `diff_id`, an id of the PR's changes at that commit against where it branched
  off the base branch (item_sources.github.diff_id: the added and removed lines of each file, without line numbers or
  context). Two commits with the same id change the same lines: a rebase, or a conflict fixed without changing the
  PR's own lines. NULL when GitHub could not give the whole diff, which counts as changed. Read once per commit, only
  for PRs of a task a person OK'd.
"""


def up(conn):
    conn.execute(
        "CREATE TABLE IF NOT EXISTS github_pr_diffs ("
        "repo TEXT NOT NULL, "
        "sha TEXT NOT NULL, "
        "diff_id TEXT, "
        "read_at TEXT NOT NULL, "
        "PRIMARY KEY (repo, sha))"
    )
