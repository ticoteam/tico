"""File search matches words by their start, as docs search does (backend/file_library.py candidates).

`file_text` stemmed words with porter, so "harness" was held as "har" and no word-start query found it. It is made again
with `unicode61` (whole words), each row kept with its text and rowid. A second run changes nothing.
"""


def up(conn):
    sql = conn.execute("SELECT sql FROM sqlite_master WHERE name='file_text'").fetchone()
    if sql and "porter" in sql[0]:
        conn.execute("ALTER TABLE file_text RENAME TO file_text_old")
        conn.execute("CREATE VIRTUAL TABLE file_text USING fts5(title, text, tokenize='unicode61')")
        conn.execute("INSERT INTO file_text(rowid,title,text) SELECT rowid,title,text FROM file_text_old")
        conn.execute("DROP TABLE file_text_old")
