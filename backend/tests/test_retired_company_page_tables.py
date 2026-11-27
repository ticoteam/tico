"""The retired Company page's tables are dropped (C-2 follow-up): who its readers were, their sessions, invites,
messages and what they read, and the company's editions, rules, stats and approvals. A dormant table still held
personal data; now no database keeps it, new or updated."""

import sqlite3
from contextlib import closing

from backend import migrations

DROP = "20261127103214_drop_retired_company_page_tables"
TABLES = {
    "company_page_settings",
    "audiences",
    "audience_sources",
    "disclosure_revisions",
    "disclosure_rules",
    "editions",
    "edition_sources",
    "edition_checks",
    "readers",
    "audience_readers",
    "reader_sessions",
    "reader_invites",
    "edition_views",
    "reader_messages",
    "reader_notices",
    "page_stats",
    "stat_board_approvals",
    "page_releases",
}


def _tables(conn):
    return {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}


def _page_tables_from_the_baseline(conn):
    """Make the tables again as a database updated from before the drop has them, with their indexes."""
    sql = (migrations.DIRECTORY / migrations.BASELINE).read_text(encoding="utf-8")
    for statement in migrations.statements(sql):
        head = statement.split("(", 1)[0].split()
        if head[:2] == ["CREATE", "TABLE"] and head[2] in TABLES:
            conn.execute(statement)
        elif "INDEX" in head[:3] and statement.split(" ON ", 1)[1].split("(", 1)[0].strip() in TABLES:
            conn.execute(statement)


def test_a_new_database_has_none_of_the_company_page_tables():
    with closing(sqlite3.connect(":memory:", isolation_level=None)) as conn:
        migrations.apply(conn)
        assert _tables(conn) & TABLES == set()


def test_an_updated_database_loses_the_tables_their_rows_and_indexes(tmp_path):
    with closing(sqlite3.connect(tmp_path / "hub.db", isolation_level=None)) as conn:
        migrations.apply(conn)
        conn.execute("DELETE FROM schema_migrations WHERE version=?", (DROP,))
        _page_tables_from_the_baseline(conn)
        assert _tables(conn) >= TABLES
        conn.execute("PRAGMA foreign_keys=ON")
        now = "2026-11-01T00:00:00Z"
        conn.execute(
            "INSERT INTO audiences (id, name, kind, created_at, updated_at) VALUES ('board','Board','board',?,?)",
            (now, now),
        )
        conn.execute(
            "INSERT INTO readers (id, email, added_by, created_at) VALUES ('r1','ana@example.com','owner',?)", (now,)
        )
        conn.execute(
            "INSERT INTO audience_readers (id, audience_id, reader_id, created_at) VALUES ('ar1','board','r1',?)",
            (now,),
        )

        migrations.apply(conn)

        assert _tables(conn) & TABLES == set()
        indexes = {r[0] for r in conn.execute("SELECT tbl_name FROM sqlite_master WHERE type='index'")}
        assert indexes & TABLES == set()
        assert conn.execute("SELECT 1 FROM schema_migrations WHERE version=?", (DROP,)).fetchone()
        assert list(conn.execute("PRAGMA foreign_key_check")) == []
