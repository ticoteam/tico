"""Finance by segment (spec: Money). A budget and a customer get a segment (fin_budgets.segment_id,
fin_contacts.segment_id, and fin_contacts.segment_sure: 1 when a person set it, 0 for a guess). A line tag may be kind
`segment`, so fin_line_tags is rebuilt with that kind in its CHECK: its rows and its index come across as they are.
The view defaults gain a `segment_lead` role (backend/finance/core/view.py Defaults), which reads as "lead" when it has
no row, so only the view version moves here, so cached views and briefings follow."""

FOREIGN_KEYS_OFF = True

LEGACY_KINDS = "'location','class')"
WIDE_KINDS = "'location','class','segment')"


def _ordered(conn, table):
    return [row[1] for row in conn.execute(f"PRAGMA table_info({table})")]


def _add(conn, table, column, ddl):
    if column not in _ordered(conn, table):
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}")


def _widen_line_tags(conn):
    """fin_line_tags rebuilt with `segment` in its kind CHECK: the same columns in the same order, its rows copied and
    its index made again (a dropped table takes its indexes with it)."""
    sql = conn.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='fin_line_tags'").fetchone()[0]
    if "'segment'" in sql:
        return
    if "'location'" not in sql:
        # An install below cloud step 76 has no `location` kind either. Step 76 rebuilds the table from its current
        # definition, which already has `segment`, so there is nothing to widen here.
        return
    if LEGACY_KINDS not in sql:
        raise RuntimeError("fin_line_tags has a kind CHECK this migration does not know: " + sql)
    head = "CREATE TABLE fin_line_tags"
    if not sql.startswith(head):
        raise RuntimeError("fin_line_tags has a definition this migration does not know: " + sql[:80])
    indexes = [
        row[0]
        for row in conn.execute(
            "SELECT sql FROM sqlite_master WHERE type='index' AND tbl_name='fin_line_tags' AND sql IS NOT NULL"
        )
    ]
    columns = ",".join(f'"{name}"' for name in _ordered(conn, "fin_line_tags"))
    widened = sql[len(head) :].replace(LEGACY_KINDS, WIDE_KINDS, 1)
    conn.execute("CREATE TABLE fin_line_tags_widened" + widened)
    conn.execute(f"INSERT INTO fin_line_tags_widened({columns}) SELECT {columns} FROM fin_line_tags")
    conn.execute("DROP TABLE fin_line_tags")
    conn.execute("ALTER TABLE fin_line_tags_widened RENAME TO fin_line_tags")
    for statement in indexes:
        conn.execute(statement)


def up(conn):
    tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    if "fin_budgets" in tables:
        _add(conn, "fin_budgets", "segment_id", "TEXT")
    if "fin_contacts" in tables:
        _add(conn, "fin_contacts", "segment_id", "TEXT")
        _add(conn, "fin_contacts", "segment_sure", "INTEGER NOT NULL DEFAULT 0")
    if "fin_line_tags" in tables:
        _widen_line_tags(conn)
    if "fin_view_versions" in tables:
        at = conn.execute("SELECT strftime('%Y-%m-%dT%H:%M:%SZ','now')").fetchone()[0]
        conn.execute(
            "INSERT INTO fin_view_versions(id, version, reason, updated_at) VALUES(1, 1, 'defaults', ?) "
            "ON CONFLICT(id) DO UPDATE SET version=fin_view_versions.version+1, reason='defaults', "
            "updated_at=excluded.updated_at",
            (at,),
        )
