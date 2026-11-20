"""A skill version Tico approved: its library skills in each company's directory (backend/skill_library.py,
docs/skills.md). Such a version came from no proposal and no person approved it, so `skill_versions.proposal_id` and
`skill_versions.approved_by_id` may now be NULL, both NULL for Tico's version; `approved_by_id` still references the
person who approved any other one.

SQLite cannot drop NOT NULL in place, so the table is built again with the same columns and rows (the rowid kept), the
references to it from other tables left as they are. Running this again changes nothing.
"""

FOREIGN_KEYS_OFF = True

COLUMNS = (
    "id",
    "skill_id",
    "number",
    "sha256",
    "files",
    "description",
    "proposal_id",
    "approved_by_id",
    "approved_at",
    "created_at",
)

TABLE = """CREATE TABLE skill_versions_new (
  id TEXT PRIMARY KEY,                       -- 'skv_' + random
  skill_id TEXT NOT NULL REFERENCES skills(id),
  number INTEGER NOT NULL,                   -- 1, 2, 3 ...
  sha256 TEXT NOT NULL,                      -- of the canonical bundle (sorted [{path, content}] JSON)
  files TEXT NOT NULL,                       -- JSON [{path, content}], immutable
  description TEXT NOT NULL DEFAULT '',
  proposal_id TEXT,                          -- NULL: Tico's library version
  approved_by_id TEXT REFERENCES humans(id), -- NULL: Tico approved it (its library version)
  approved_at TEXT NOT NULL,
  created_at TEXT NOT NULL,
  UNIQUE(skill_id, number),
  UNIQUE(skill_id, sha256)
)"""


def up(conn):
    info = {row[1]: row for row in conn.execute("PRAGMA table_info(skill_versions)")}
    if not info:
        raise RuntimeError("The skill_versions migration found no skill_versions table.")
    if set(info) != set(COLUMNS):
        raise RuntimeError(f"The skill_versions migration found unexpected columns: {sorted(set(info) ^ set(COLUMNS))}")
    if not info["approved_by_id"][3] and not info["proposal_id"][3]:
        return  # both nullable already
    indexes = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='index' AND tbl_name='skill_versions' AND sql IS NOT NULL"
    ).fetchall()
    # SQLite rechecks every trigger and view during the rename, including ones that use tables built after migrations.
    aside = conn.execute(
        "SELECT type, name, sql FROM sqlite_master WHERE type IN ('trigger', 'view') AND sql IS NOT NULL"
    ).fetchall()
    for kind, name, _ in aside:
        conn.execute(f'DROP {kind.upper()} IF EXISTS "{name}"')
    conn.execute(TABLE)
    columns = ", ".join(("rowid", *COLUMNS))
    conn.execute(f"INSERT INTO skill_versions_new ({columns}) SELECT {columns} FROM skill_versions")
    conn.execute("DROP TABLE skill_versions")
    conn.execute("ALTER TABLE skill_versions_new RENAME TO skill_versions")
    for (statement,) in indexes:
        conn.execute(statement)
    for _, _, statement in aside:
        conn.execute(statement)
