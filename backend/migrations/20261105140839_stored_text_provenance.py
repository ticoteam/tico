"""Retain the sources of generated scalar text independently of its record's schema."""


def up(conn):
    conn.execute("CREATE TABLE IF NOT EXISTS sourced_text_records ("
                 "kind TEXT NOT NULL, record_id TEXT NOT NULL, field TEXT NOT NULL, refs TEXT NOT NULL, "
                 "PRIMARY KEY(kind,record_id,field))")
