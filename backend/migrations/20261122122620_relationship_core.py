"""Relationship core: an outside party's status, memory page and tasks (backend/entities/store.py, set_status).

- `entities.status_version`: the compare-and-set counter that guards a relationship's status. Status, next step, next
  step due and flags are entity values (built-in attributes on organization and person, below), written together.
- `tasks.entity_id`: the Contacts entity a task is for ('' for none), listed by entity and moved by a merge.
- One memory page per entity: a partial unique index on page_links(target) WHERE role = 'memory'.

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

ATTRIBUTES = (
    ("status", "Status", "text", 0, "Where the relationship stands, in a word or two: lead, active, at risk, churned."),
    ("next_step", "Next step", "text", 0, "The one next thing to do for this relationship."),
    ("next_step_due", "Next step due", "date", 0, "When the next step is due."),
    ("flags", "Flags", "text", 1, "Short markers every reader of the relationship may see: vip, billing issue."),
)


def up(conn):
    columns = {row[1] for row in conn.execute("PRAGMA table_info(tasks)")}
    for column, kind in TASK_COLUMNS:
        if column not in columns:
            conn.execute(f"ALTER TABLE tasks ADD COLUMN {column} {kind}")
    conn.execute("CREATE INDEX tasks_entity ON tasks(entity_id) WHERE entity_id <> ''")

    conn.execute("ALTER TABLE entities ADD COLUMN status_version INTEGER NOT NULL DEFAULT 0")

    for statement in PAGE_LINKS:
        conn.execute(statement)
    conn.execute("CREATE UNIQUE INDEX page_links_memory ON page_links(target) WHERE role = 'memory'")

    for type_key in ("organization", "person"):
        for position, (key, name, value_type, multiple, description) in enumerate(ATTRIBUTES, start=20):
            conn.execute(
                "INSERT OR IGNORE INTO entity_attributes (id, entity_type, key, name, value_type, multiple, "
                "unique_value, required, options, owner_type, owner_id, description, sensitive, position, "
                "created_by_type, created_by_id, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, 0, 0, '[]', 'tico', '', ?, 0, ?, 'system', '', '2000-01-01T00:00:00.000000Z')",
                (f"attr_{type_key}_{key}", type_key, key, name, value_type, multiple, description, position),
            )
