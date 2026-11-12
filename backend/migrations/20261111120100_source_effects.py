"""Source actions (docs/pipelines-v3.md "Sources and items"): what entering a stage owes a source (`act`, e.g.
`github.queue`) and what leaving it owes back (`undo`), for one item, `pinned` to the item version it was owed for (a
PR's head). The table is made again: rows owed before go, as nothing is in flight on 0.6 that the next entry would not
owe again. A stage that queued its PRs (`{"queue_prs": true}`) acts on GitHub now: `{"act": "github.queue"}`.
"""

FOREIGN_KEYS_OFF = True


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


EFFECTS = (
    "DROP TABLE IF EXISTS effects",
    "CREATE TABLE effects ("
    "id TEXT PRIMARY KEY, visit_id TEXT NOT NULL REFERENCES visits(id), "
    "kind TEXT NOT NULL CHECK (kind IN ('act', 'undo')), source TEXT NOT NULL, action TEXT NOT NULL, "
    "item_type TEXT NOT NULL, item_key TEXT NOT NULL COLLATE NOCASE, pinned TEXT, key TEXT NOT NULL UNIQUE, "
    "state TEXT NOT NULL DEFAULT 'owed' CHECK (state IN ('owed', 'done', 'failed', 'moot')), "
    "tries INTEGER NOT NULL DEFAULT 0, last_error TEXT, next_try_at TEXT, "
    "created_at TEXT NOT NULL, updated_at TEXT NOT NULL)",
    "CREATE INDEX IF NOT EXISTS effects_owed ON effects(state, updated_at) WHERE state = 'owed'",
    "CREATE INDEX IF NOT EXISTS effects_visit ON effects(visit_id, kind)",
)


def up(conn):
    if "stages" in _tables(conn):
        conn.execute(
            "UPDATE stages SET on_enter=? WHERE json_valid(on_enter) AND json_extract(on_enter, '$.queue_prs') = 1",
            ('{"act": "github.queue"}',),
        )
    if "effects" not in _tables(conn) or "action" in _columns(conn, "effects"):
        return
    aside = _aside(conn)
    for statement in EFFECTS:
        conn.execute(statement)
    _back(conn, aside)
