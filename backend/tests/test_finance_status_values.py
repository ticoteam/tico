"""The finance tables' status upgrade: a CHECK rebuild that maps retired spellings, keeps child rows and refuses
values it cannot place. In a test_finance_ file, so the finance lint allows the direct table writes it needs."""
import sqlite3

import pytest

from backend import status_values as SV


def _upgrade_as_migrate_runs_it(conn):
    conn.execute("PRAGMA foreign_keys=OFF")
    conn.execute("BEGIN IMMEDIATE")
    try:
        SV.upgrade(conn)
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise
    finally:
        conn.execute("PRAGMA foreign_keys=ON")


def _old_file(path):
    conn = sqlite3.connect(path, isolation_level=None)
    conn.execute("PRAGMA foreign_keys=ON")
    conn.executescript("""
        CREATE TABLE fin_transfers (
          id TEXT PRIMARY KEY, amount BIGINT NOT NULL CHECK (amount > 0),
          from_account TEXT NOT NULL, to_account TEXT NOT NULL,
          status TEXT NOT NULL DEFAULT 'queued' CHECK (status IN ('queued','submitted','unknown','failed','posted','cancelled')),
          CHECK (from_account <> to_account));
        CREATE TABLE fin_transfer_notes(id TEXT PRIMARY KEY,
          transfer_id TEXT NOT NULL REFERENCES fin_transfers(id) ON DELETE CASCADE);
    """)
    return conn


def test_the_check_rebuild_maps_spellings_keeps_child_rows_and_is_idempotent(tmp_path):
    conn = _old_file(tmp_path / "old.db")
    conn.execute("INSERT INTO fin_transfers(id,amount,from_account,to_account,status) VALUES('t1',5,'a','b','cancelled')")
    conn.execute("INSERT INTO fin_transfers(id,amount,from_account,to_account,status) VALUES('t2',5,'a','b','posted')")
    conn.execute("INSERT INTO fin_transfer_notes(id,transfer_id) VALUES('n1','t1')")
    _upgrade_as_migrate_runs_it(conn)
    assert conn.execute("SELECT id,status FROM fin_transfers ORDER BY id").fetchall() == [("t1", "canceled"), ("t2", "posted")]
    assert conn.execute("SELECT COUNT(*) FROM fin_transfer_notes").fetchone()[0] == 1, "the rebuild must not cascade"
    sql_before = conn.execute("SELECT sql FROM sqlite_master WHERE name='fin_transfers'").fetchone()[0]
    assert "'cancelled'" not in sql_before and "status_values_status" in sql_before
    with pytest.raises(sqlite3.IntegrityError, match="status_values_status"):
        conn.execute("INSERT INTO fin_transfers(id,amount,from_account,to_account,status) VALUES('t3',5,'a','b','cancelled')")
    SV.enforce(conn)
    assert conn.execute("SELECT sql FROM sqlite_master WHERE name='fin_transfers'").fetchone()[0] == sql_before
