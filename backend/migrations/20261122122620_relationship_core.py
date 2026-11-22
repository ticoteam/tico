"""Relationship core: an outside party's status, memory page and tasks (backend/entities/store.py, set_status).

- `entities.relationship_status` (JSON: status, next_step, flags), `entities.next_step_due` (YYYY-MM-DD, sorted on by
  what's next) and `entities.status_version`, the compare-and-set counter a status write names. A merge moves none of
  them: each entity keeps its own.
- `tasks.entity_id`: the Contacts entity a task is for ('' for none), listed by entity and moved by a merge.
- One memory page per entity: a partial unique index on page_links(target) for role 'memory' on entity: targets only
  (a task document may be named Memory, more than once).

An install may still have 20261117015544_task_prefix_and_moved_visits (tasks.human_id, tasks.prefix) and
20261117195614_page_links (the page_links table) pending. Run newest first, this file would otherwise put entity_id
before those columns, or index a table not made yet. So it adds their columns first with the same definitions, and makes
page_links with the same statements; each of those files skips what is already there.
"""

TASK_COLUMNS = (
    ("human_id", "TEXT"),
    ("prefix", "TEXT"),
    ("entity_id", "TEXT NOT NULL DEFAULT ''"),
)

# 20261117195614_page_links's CREATE_LINKS, word for word.
PAGE_LINKS = (
    """CREATE TABLE IF NOT EXISTS page_links (
      id TEXT PRIMARY KEY,
      page_id TEXT NOT NULL REFERENCES bot_files(id) ON DELETE CASCADE,
      target TEXT NOT NULL,
      role TEXT NOT NULL,
      created_by TEXT NOT NULL,
      created_at TEXT NOT NULL,
      UNIQUE(page_id, target, role)
    )""",
    "CREATE INDEX IF NOT EXISTS page_links_target ON page_links(target, role)",
    "CREATE INDEX IF NOT EXISTS page_links_page ON page_links(page_id)",
    "CREATE INDEX IF NOT EXISTS page_links_role ON page_links(role, page_id)",
)


def up(conn):
    columns = {row[1] for row in conn.execute("PRAGMA table_info(tasks)")}
    for column, kind in TASK_COLUMNS:
        if column not in columns:
            conn.execute(f"ALTER TABLE tasks ADD COLUMN {column} {kind}")
    conn.execute("CREATE INDEX tasks_entity ON tasks(entity_id) WHERE entity_id <> ''")

    conn.execute("ALTER TABLE entities ADD COLUMN relationship_status TEXT NOT NULL DEFAULT '{}'")
    conn.execute("ALTER TABLE entities ADD COLUMN next_step_due TEXT")
    conn.execute("ALTER TABLE entities ADD COLUMN status_version INTEGER NOT NULL DEFAULT 0")
    conn.execute("CREATE INDEX entities_next_step ON entities(owner_id, next_step_due) WHERE next_step_due IS NOT NULL")

    for statement in PAGE_LINKS:
        conn.execute(statement)
    conn.execute(
        "CREATE UNIQUE INDEX page_links_memory ON page_links(target) WHERE role = 'memory' AND target LIKE 'entity:%'"
    )
