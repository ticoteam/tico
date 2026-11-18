"""Backfill page links without changing the file's read boundary."""

CREATE_LINKS = (
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

WRITERS = """
SELECT lower(hex(randomblob(16))), p.file_id, 'bot:' || f.bot_id, 'writer',
       'bot:' || f.bot_id, p.created_at
FROM pages p JOIN bot_files f ON f.id=p.file_id
"""
DOCUMENT_ROLES = """
SELECT lower(hex(randomblob(16))), d.file_id, 'task:' || d.task_id,
       CASE lower(trim(d.name)) WHEN 'related' THEN 'Related document' WHEN 'writer' THEN 'Writer document' ELSE d.name END,
       'bot:' || f.bot_id, d.created_at
FROM task_documents d JOIN bot_files f ON f.id=d.file_id
"""
TASK_FILES = """
SELECT lower(hex(randomblob(16))), p.file_id, 'task:' || f.task_id,
       CASE WHEN p.spec IS NOT NULL AND p.spec<>'' THEN 'Spec' ELSE 'related' END,
       'bot:' || f.bot_id, p.created_at
FROM pages p JOIN bot_files f ON f.id=p.file_id WHERE f.task_id IS NOT NULL AND f.task_id<>''
"""
PINS = """
SELECT 'pin:' || pin.id, p.file_id, pin.place, 'related', pin.made_by, pin.created_at
FROM pins pin JOIN pages p ON pin.item='page:' || p.file_id WHERE pin.place LIKE 'task:%'
"""
HUMAN_WRITERS = """
SELECT lower(hex(randomblob(16))), v.file_id, v.actor, 'writer', v.actor, min(v.created_at)
FROM bot_file_versions v JOIN pages p ON p.file_id=v.file_id
WHERE v.actor LIKE 'human:%' GROUP BY v.file_id,v.actor
"""


def up(conn):
    for statement in CREATE_LINKS:
        conn.execute(statement)

    # An older company may reach this migration before it has the legacy table. Only its existing documents need adopting.
    if conn.execute("SELECT 1 FROM sqlite_schema WHERE type='table' AND name='task_documents'").fetchone():
        conn.execute(
            "INSERT OR IGNORE INTO pages(file_id,cadence,created_at,updated_at) "
            "SELECT d.file_id,'none',min(d.created_at),max(d.updated_at) "
            "FROM task_documents d JOIN bot_files f ON f.id=d.file_id GROUP BY d.file_id"
        )
        conn.execute("INSERT OR IGNORE INTO page_links " + DOCUMENT_ROLES)
    # Prefer a related link backed by the filing to one backed solely by a pin.
    for source in (WRITERS, TASK_FILES, PINS, HUMAN_WRITERS):
        conn.execute("INSERT OR IGNORE INTO page_links " + source)

    # Keep the existing name so the older CREATE INDEX IF NOT EXISTS cannot restore one page per requirement.
    conn.execute("DROP INDEX IF EXISTS requirement_pages_current")
    conn.execute(
        "CREATE UNIQUE INDEX requirement_pages_current "
        "ON requirement_pages(visit_id, requirement_id, file_id) WHERE replaced_at IS NULL"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS requirement_pages_history "
        "ON requirement_pages(visit_id, requirement_id, created_at)"
    )
