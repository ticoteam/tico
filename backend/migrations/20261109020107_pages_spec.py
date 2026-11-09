"""pages.spec (backend/visual_specs.py): "card" marks a visual spec on a Product roadmap card. Other pages stay NULL."""


def up(conn):
    columns = {row[1] for row in conn.execute("PRAGMA table_info(pages)")}
    if columns and "spec" not in columns:
        conn.execute("ALTER TABLE pages ADD COLUMN spec TEXT")
