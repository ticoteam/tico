"""Docs search ranks sections with bm25 in SQLite (backend/docs.py index, manual.best_sections).

`doc_sections_fts` holds one row per section of each live doc: its title, path, heading and text, the rowid the doc's
rowid shifted left by manual.SECTION_BITS plus the section's position. It replaces `docs_fts` (one row per doc) and
`docs.search_parse` (the parse Python ranked by), which nothing reads any more. The table starts empty: the next start
fills it (docs.ensure_schema). Each step checks first, so a second run changes nothing.
"""


def up(conn):
    conn.execute("DROP TABLE IF EXISTS docs_fts")
    conn.execute(
        "CREATE VIRTUAL TABLE IF NOT EXISTS doc_sections_fts "
        "USING fts5(title, path, heading, body, tokenize='porter unicode61')"
    )
    if "search_parse" in {row[1] for row in conn.execute("PRAGMA table_info(docs)")}:
        conn.execute("ALTER TABLE docs DROP COLUMN search_parse")
