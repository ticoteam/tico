"""Generated SQL/Python migrations load and run, without reusing timestamps or overwriting files."""
from datetime import datetime, timezone
import sqlite3

import pytest

from backend import migrations
from scripts import new_migration


def test_generator_uses_utc_and_skips_occupied_seconds_across_both_formats(tmp_path, monkeypatch, capsys):
    class Clock:
        @staticmethod
        def now(zone):
            assert zone == timezone.utc
            return datetime(2026, 12, 31, 23, 59, 59, tzinfo=zone)

    monkeypatch.setattr(new_migration, "DIRECTORY", tmp_path)
    monkeypatch.setattr(new_migration, "datetime", Clock)
    occupied = tmp_path / "20261231235959_existing.py"
    occupied.write_text("def up(conn): pass\n")
    assert new_migration.main(["first"]) == 0
    assert new_migration.main(["second", "--py"]) == 0
    assert capsys.readouterr().out.splitlines() == [str(tmp_path / name) for name in (
        "20270101000000_first.sql", "20270101000001_second.py")]
    assert occupied.read_text() == "def up(conn): pass\n"
    found = migrations.files(tmp_path)
    assert [m.version for m in found] == ["20261231235959_existing", "20270101000000_first", "20270101000001_second"]
    assert "FOREIGN_KEYS_OFF" in found[-1].path.read_text()
    conn = sqlite3.connect(":memory:", isolation_level=None)
    try:
        migrations.apply(conn, tmp_path)
        assert conn.execute("SELECT version FROM schema_migrations ORDER BY version").fetchall() == [
            (m.version,) for m in found]
    finally:
        conn.close()


def test_generator_refuses_a_name_that_would_escape_the_migration_directory(tmp_path, monkeypatch):
    monkeypatch.setattr(new_migration, "DIRECTORY", tmp_path)
    with pytest.raises(SystemExit) as exc:
        new_migration.main(["../escape", "--py"])
    assert exc.value.code == 2
    assert list(tmp_path.iterdir()) == []
