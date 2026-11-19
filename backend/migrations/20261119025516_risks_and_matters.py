"""Risks and matters (Risk plan, slice 1; backend/risk.py).

- `tags.kind`: '' for a plain tag, 'risk' for a risk. A risk is a tag of kind risk, `risk:{slug}`; any task can carry it.
- `matters`: the facts of one task tagged with a risk: what kind of matter it is, the party, the amount claimed (whole
  minor units, like Finance), the claim, whose claim it is, the forum, the place and product it is about. One row per
  task; it goes with the task.
- `risk_links`: the rules and documents linked to a risk. A rule is an `org_rule` (role `rule`); a document is a
  Directory `entity` (role `source`, or `covers` for a policy that covers the risk).

Running it again changes nothing.
"""


def up(conn):
    if "kind" not in {row[1] for row in conn.execute("PRAGMA table_info(tags)")}:
        conn.execute("ALTER TABLE tags ADD COLUMN kind TEXT NOT NULL DEFAULT ''")
    conn.execute(
        "CREATE TABLE IF NOT EXISTS matters ("
        "task_id TEXT PRIMARY KEY REFERENCES tasks(id) ON DELETE CASCADE, "
        "kind TEXT NOT NULL CHECK (kind IN ('dispute','request','notice','incident')), "
        "party_entity_id TEXT REFERENCES entities(id) ON DELETE SET NULL, "
        "amount INTEGER, currency TEXT, claim TEXT NOT NULL DEFAULT '', "
        "side TEXT NOT NULL DEFAULT 'theirs' CHECK (side IN ('theirs','ours')), "
        "forum TEXT NOT NULL DEFAULT '', "
        "place_id TEXT REFERENCES entities(id) ON DELETE SET NULL, "
        "product_id TEXT REFERENCES entities(id) ON DELETE SET NULL, "
        "version INTEGER NOT NULL DEFAULT 1, "
        "created_by_id TEXT NOT NULL, created_at TEXT NOT NULL, updated_by_id TEXT NOT NULL, updated_at TEXT NOT NULL)"
    )
    conn.execute("CREATE INDEX IF NOT EXISTS matters_kind ON matters(kind)")
    conn.execute(
        "CREATE TABLE IF NOT EXISTS risk_links ("
        "id TEXT PRIMARY KEY, risk_id TEXT NOT NULL REFERENCES tags(id) ON DELETE CASCADE, "
        "linkable_type TEXT NOT NULL CHECK (linkable_type IN ('org_rule','entity')), "
        "linkable_id TEXT NOT NULL, "
        "role TEXT NOT NULL CHECK (role IN ('rule','source','covers')), "
        "created_by_id TEXT NOT NULL, created_at TEXT NOT NULL, "
        "UNIQUE (risk_id, linkable_type, linkable_id, role))"
    )
    conn.execute("CREATE INDEX IF NOT EXISTS risk_links_linkable ON risk_links(linkable_type, linkable_id)")
