"""The facts the Product and Engineering pipelines read (docs/pipelines-v3.md "Facts", "GitHub"; docs/dev/pipelines-v3-build.md).

- pr_facts: Tico's saved copy of one pull request: its newest commit, its checks there, whether it is in the merge queue,
  whether it merged. `queued_at` is when Tico first saw it queued; `removed_at`, `removed_reason`, `failing_check` and
  `failing_check_url` say when and why GitHub took it out of the queue without merging it. One row per PR; a task reaches its PRs through its `task_links` (kind `pr`). `fact_version` is the
  time the read or webhook that saw it began, in ms: an older one never replaces a newer one (backend/task_facts.py).
- releases: a published release of a repo (never a draft): its tag, its commit, whether it is a pre-release.
- release_reads: when a repo's releases were last read, so "this repo publishes no releases" (C31) is a fact, not a guess.
- release_commits: whether a commit is in a release's history (GitHub's compare), read once per pair.
- github_deliveries: the webhook deliveries already taken (`X-GitHub-Delivery`): a repeat is dropped.
- requirement_pages: the page attached to a page requirement for one visit, at one version. The newest that is not
  replaced counts. `heads` is every linked PR's newest commit when it was attached ("for the newest commit").
- page_verdicts: Jev's verdict on one page version against one rubric (and what else it was shown, in `rubric_hash`).
- effects: + the PR an effect is for, the commit it queues, and when it may be tried again.

No foreign key to `tasks`. On a new file every table is made here; every step checks first, so a second run changes
nothing.
"""

TABLES = (
    "CREATE TABLE IF NOT EXISTS pr_facts ("
    "id TEXT PRIMARY KEY, repo TEXT NOT NULL COLLATE NOCASE, number INTEGER NOT NULL, "
    "state TEXT NOT NULL CHECK (state IN ('open', 'closed', 'merged')), "
    "draft INTEGER NOT NULL DEFAULT 0 CHECK (draft IN (0, 1)), head_sha TEXT, head_seen_at TEXT, "
    "checks_state TEXT NOT NULL DEFAULT 'none' CHECK (checks_state IN ('none', 'pending', 'success', 'failure')), "
    "checks TEXT NOT NULL DEFAULT '[]' CHECK (json_valid(checks)), checks_started_at TEXT, "
    "merged_sha TEXT, merged_at TEXT, "
    "queue_state TEXT NOT NULL DEFAULT 'none' CHECK (queue_state IN ('none', 'queued', 'auto_merge', 'removed')), "
    "queued_at TEXT, removed_at TEXT, removed_reason TEXT, failing_check TEXT, failing_check_url TEXT, "
    "fact_version INTEGER NOT NULL, read_at TEXT NOT NULL, "
    "created_at TEXT NOT NULL, updated_at TEXT NOT NULL)",
    "CREATE UNIQUE INDEX IF NOT EXISTS pr_facts_pr ON pr_facts(repo, number)",
    "CREATE INDEX IF NOT EXISTS pr_facts_read ON pr_facts(read_at) WHERE state = 'open'",
    "CREATE TABLE IF NOT EXISTS releases ("
    "id TEXT PRIMARY KEY, repo TEXT NOT NULL COLLATE NOCASE, tag TEXT NOT NULL, commit_sha TEXT, "
    "prerelease INTEGER NOT NULL DEFAULT 0 CHECK (prerelease IN (0, 1)), published_at TEXT NOT NULL, "
    "fact_version INTEGER NOT NULL, read_at TEXT NOT NULL, created_at TEXT NOT NULL)",
    "CREATE UNIQUE INDEX IF NOT EXISTS releases_tag ON releases(repo, tag)",
    "CREATE TABLE IF NOT EXISTS release_reads ("
    "repo TEXT PRIMARY KEY COLLATE NOCASE, read_at TEXT NOT NULL, fact_version INTEGER NOT NULL)",
    "CREATE TABLE IF NOT EXISTS release_commits ("
    "release_id TEXT NOT NULL REFERENCES releases(id), commit_sha TEXT NOT NULL, "
    "included INTEGER NOT NULL CHECK (included IN (0, 1)), checked_at TEXT NOT NULL, "
    "PRIMARY KEY (release_id, commit_sha))",
    "CREATE TABLE IF NOT EXISTS github_deliveries ("
    "id TEXT PRIMARY KEY, event TEXT NOT NULL, received_at TEXT NOT NULL)",
    "CREATE INDEX IF NOT EXISTS github_deliveries_received ON github_deliveries(received_at)",
    "CREATE TABLE IF NOT EXISTS requirement_pages ("
    "id TEXT PRIMARY KEY, visit_id TEXT NOT NULL REFERENCES visits(id), "
    "requirement_id TEXT NOT NULL REFERENCES requirements(id), file_id TEXT NOT NULL, version INTEGER NOT NULL, "
    "heads TEXT NOT NULL DEFAULT '{}' CHECK (json_valid(heads)), attached_by_id TEXT NOT NULL, "
    "created_at TEXT NOT NULL, replaced_at TEXT)",
    "CREATE UNIQUE INDEX IF NOT EXISTS requirement_pages_current ON requirement_pages(visit_id, requirement_id) "
    "WHERE replaced_at IS NULL",
    "CREATE INDEX IF NOT EXISTS requirement_pages_file ON requirement_pages(file_id, version)",
    "CREATE TABLE IF NOT EXISTS page_verdicts ("
    "id TEXT PRIMARY KEY, file_id TEXT NOT NULL, version INTEGER NOT NULL, rubric_hash TEXT NOT NULL, "
    "verdict TEXT CHECK (verdict IS NULL OR verdict IN ('met', 'not_met', 'unsure', 'failed')), "
    "p REAL, reasons TEXT, model TEXT, cost_usd REAL NOT NULL DEFAULT 0, tries INTEGER NOT NULL DEFAULT 0, "
    "last_error TEXT, next_try_at TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL)",
    "CREATE UNIQUE INDEX IF NOT EXISTS page_verdicts_key ON page_verdicts(file_id, version, rubric_hash)",
    "CREATE INDEX IF NOT EXISTS page_verdicts_owed ON page_verdicts(next_try_at) WHERE verdict IS NULL",
)

EFFECT_COLUMNS = (("repo", "TEXT"), ("number", "INTEGER"), ("head_sha", "TEXT"), ("next_try_at", "TEXT"))


def up(conn):
    for statement in TABLES:
        conn.execute(statement)
    effects = {row[1] for row in conn.execute("PRAGMA table_info(effects)")}
    if effects:
        for name, kind in EFFECT_COLUMNS:
            if name not in effects:
                conn.execute(f"ALTER TABLE effects ADD COLUMN {name} {kind}")
