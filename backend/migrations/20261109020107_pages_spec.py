"""pages.spec (backend/spec_approval.py): which pages are visual specs on a Product roadmap card ("card") or their frozen
copies on a Dev ticket ("frozen"). Older pages stay NULL."""


def up(conn):
    columns = {row[1] for row in conn.execute("PRAGMA table_info(pages)")}
    if columns and "spec" not in columns:
        conn.execute("ALTER TABLE pages ADD COLUMN spec TEXT")
