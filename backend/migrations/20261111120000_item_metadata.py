"""Items of a source (docs/pipelines-v3.md "Sources and items"): GitHub's PRs are items of the source `github`, saved in
one generic store.

- item_metadata: Tico's saved copy of one item of a source (a PR: `source` github, `item_type` pr, `item_key`
  `owner/name#12`). `fields` is what the source said of it; `metadata_version` (ms) is when the read or webhook that saw
  it began, and an older one never replaces a newer one. `read_at` is the last word from the source, `due_at` set when
  it must be read again on the next pass whatever its age, `final_at` when it can no longer change what a task waits on
  (a merged or closed PR). `read_error`, `refused_at` and `gone_at` are for the source refusing or losing it (not used
  yet). It replaces pr_facts, which goes with its rows: open PRs are read again on the next pass.
- task_links: + `source`, `item_type`, `item_key`: the item a link is, once per task. Existing PR links are filled in.
- requirement_pages.heads -> item_versions.
- releases, release_reads, release_commits -> github_releases, github_release_reads, github_release_commits, with
  `fact_version` -> `metadata_version`.
"""


def _tables(conn):
    return {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}


def _columns(conn, table):
    return {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}


def _aside(conn):
    """A rebuild or rename re-checks every trigger and view, and on a new file a trigger can name a table the module
    code makes after the migrations. So they step aside, and go back as they were (`_back`)."""
    aside = conn.execute(
        "SELECT type, name, sql FROM sqlite_master WHERE type IN ('trigger', 'view') AND sql IS NOT NULL"
    ).fetchall()
    for kind, name, _sql in aside:
        conn.execute(f'DROP {kind.upper()} IF EXISTS "{name}"')
    return aside


def _back(conn, aside):
    for _kind, _name, sql in aside:
        conn.execute(sql)


ITEM_METADATA = (
    "CREATE TABLE IF NOT EXISTS item_metadata ("
    "id TEXT PRIMARY KEY, source TEXT NOT NULL, item_type TEXT NOT NULL, item_key TEXT NOT NULL COLLATE NOCASE, "
    "title TEXT, fields TEXT NOT NULL DEFAULT '{}' CHECK (json_valid(fields)), metadata_version INTEGER NOT NULL, "
    "read_at TEXT, due_at TEXT, read_error TEXT, refused_at TEXT, gone_at TEXT, final_at TEXT, "
    "created_at TEXT NOT NULL, updated_at TEXT NOT NULL)",
    "CREATE UNIQUE INDEX IF NOT EXISTS item_metadata_item ON item_metadata(source, item_type, item_key)",
    "CREATE INDEX IF NOT EXISTS item_metadata_head ON item_metadata(source, json_extract(fields, '$.head_sha')) "
    "WHERE final_at IS NULL",
    "CREATE INDEX IF NOT EXISTS item_metadata_due ON item_metadata(due_at) WHERE due_at IS NOT NULL",
)

RELEASES = (
    ("releases", "github_releases"),
    ("release_reads", "github_release_reads"),
    ("release_commits", "github_release_commits"),
)


def up(conn):
    aside = _aside(conn)
    tables = _tables(conn)
    for statement in ITEM_METADATA:
        conn.execute(statement)
    conn.execute("DROP TABLE IF EXISTS pr_facts")
    if "task_links" in tables:
        have = _columns(conn, "task_links")
        for name, kind in (("source", "TEXT"), ("item_type", "TEXT"), ("item_key", "TEXT COLLATE NOCASE")):
            if name not in have:
                conn.execute(f"ALTER TABLE task_links ADD COLUMN {name} {kind}")
        # One item per PR per task: a duplicate link made before links matched by case keeps none.
        conn.execute(
            "UPDATE task_links SET source='github', item_type='pr', item_key=repo || '#' || number "
            "WHERE kind='pr' AND source IS NULL AND repo IS NOT NULL AND repo <> '' AND number IS NOT NULL "
            "AND id = (SELECT d.id FROM task_links d WHERE d.task_id = task_links.task_id AND d.kind='pr' "
            "AND lower(d.repo) = lower(task_links.repo) AND d.number = task_links.number ORDER BY d.created_at, d.id "
            "LIMIT 1)"
        )
        conn.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS task_links_item ON task_links(task_id, source, item_type, item_key) "
            "WHERE source IS NOT NULL"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS task_links_item_key ON task_links(source, item_type, item_key) "
            "WHERE source IS NOT NULL"
        )
    if "requirement_pages" in tables and "heads" in _columns(conn, "requirement_pages"):
        conn.execute("ALTER TABLE requirement_pages RENAME COLUMN heads TO item_versions")
    for old, new in RELEASES:
        if old in tables and new not in tables:
            conn.execute(f"ALTER TABLE {old} RENAME TO {new}")
            if "fact_version" in _columns(conn, new):
                conn.execute(f"ALTER TABLE {new} RENAME COLUMN fact_version TO metadata_version")
    if "github_releases" in _tables(conn):
        conn.execute("DROP INDEX IF EXISTS releases_tag")
        conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS github_releases_tag ON github_releases(repo, tag)")
    _back(conn, aside)
