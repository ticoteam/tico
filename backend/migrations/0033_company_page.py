"""The Company page's tables (backend/company_page/schema.py). A file from a release that ran this as hubdb's step 73
(migrations.NEWEST_USER_VERSION) already has them, renamed by 0013 and 0014, so every statement is IF NOT EXISTS. That
file's reader_notices allows only the old `cancelled`, so it is rebuilt with the one spelling."""
from backend.company_page.schema import COMPANY_PAGE_SCHEMA
from backend.migrations import statements


def up(conn):
    old = conn.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='reader_notices'").fetchone()
    rebuild = bool(old and "cancelled" in old[0])
    if rebuild:
        conn.execute("DROP INDEX IF EXISTS reader_notices_pending")
        conn.execute("ALTER TABLE reader_notices RENAME TO reader_notices_before")
    for statement in statements(COMPANY_PAGE_SCHEMA):
        conn.execute(statement)
    if rebuild:
        columns = [row[1] for row in conn.execute("PRAGMA table_info(reader_notices_before)")]
        values = ["replace(status,'cancelled','canceled')" if name == "status" else name for name in columns]
        conn.execute(f"INSERT INTO reader_notices({','.join(columns)}) SELECT {','.join(values)} FROM reader_notices_before")
        conn.execute("DROP TABLE reader_notices_before")
