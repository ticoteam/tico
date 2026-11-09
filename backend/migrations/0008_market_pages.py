"""The market notes get their own table, market_pages. They lived in `documents`, the repository mirror's table, under
collection 'market'; they are copied out, then `documents` and `document_versions` go. The mirror's other rows were
never shown anywhere, so they are not copied. A new file has no `documents` and gets the empty table."""
import json

TABLE = ("CREATE TABLE IF NOT EXISTS market_pages (id TEXT PRIMARY KEY, title TEXT NOT NULL, category TEXT NOT NULL, "
         "body TEXT NOT NULL, seeded INTEGER NOT NULL DEFAULT 0, updated_at TEXT NOT NULL)")


def up(conn):
    conn.execute(TABLE)
    if not conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='documents'").fetchone():
        return
    for doc_id, raw, updated in conn.execute(
            "SELECT id, payload_json, updated FROM documents WHERE collection='market'").fetchall():
        page = json.loads(raw)
        conn.execute("INSERT OR REPLACE INTO market_pages(id,title,category,body,seeded,updated_at) VALUES(?,?,?,?,?,?)",
                     (doc_id, page.get("title") or "", page.get("category") or "", page.get("content") or "",
                      1 if page.get("seeded") else 0, updated))
    conn.execute("DROP TABLE IF EXISTS document_versions")
    conn.execute("DROP TABLE IF EXISTS documents")
