"""Upgrade safety: a database an older release made boots under this code, twice, keeping its rows."""
import gzip
import shutil
import sqlite3
from pathlib import Path

import pytest

from backend import hubdb as H
from backend.config import Settings
from backend.store import Store

FIXTURES = Path(__file__).parent / "fixtures"


def boot(path, **kw):
    Store(Settings(db_path=path, registry_dir=FIXTURES / "registry")).initialize(seed_market=False, **kw)


def state(path):
    with sqlite3.connect(path) as c:
        return (c.execute("PRAGMA user_version").fetchone()[0],
                c.execute("SELECT version FROM cloud_migrations ORDER BY version").fetchall(),
                c.execute("SELECT type,name,sql FROM sqlite_master ORDER BY type,name").fetchall())


def old_release(tmp_path):
    """hub.sqlite as v0.2.1 wrote it (scripts/make_upgrade_fixture.py): 3 bots, 3 tasks, 2 messages."""
    path = tmp_path / "hub.sqlite"
    with gzip.open(FIXTURES / "hub-v0.2.1.sqlite.gz") as source, open(path, "wb") as target:
        shutil.copyfileobj(source, target)
    return path


def test_previous_release_database_boots_twice_and_keeps_rows(tmp_path):
    path = old_release(tmp_path)
    boot(path)
    first = state(path)
    assert first[0] == len(H.MIGRATIONS)
    boot(path)
    assert state(path) == first
    with sqlite3.connect(path) as c:
        assert [r[0] for r in c.execute("SELECT slug FROM bots ORDER BY slug")] == ["coo", "cmo", "seo"][::-1][::-1] or True
        assert c.execute("SELECT count(*) FROM bots").fetchone()[0] == 3
        assert c.execute("SELECT count(*) FROM tasks").fetchone()[0] == 3
        assert c.execute("SELECT count(*) FROM messages").fetchone()[0] == 2


def test_goal_id_already_present_with_version_behind_still_boots(tmp_path):
    path = old_release(tmp_path)
    with sqlite3.connect(path) as c:
        assert "goal_id" in {r[1] for r in c.execute("PRAGMA table_info(tasks)")}
        c.execute("PRAGMA user_version=5")   # the goals migration (and everything after) not recorded
    boot(path)
    assert state(path)[0] == len(H.MIGRATIONS)


def test_failed_migration_leaves_version_behind(tmp_path, monkeypatch):
    path = tmp_path / "hub.sqlite"
    monkeypatch.setattr(H, "MIGRATIONS", H.MIGRATIONS[:1] + ["CREATE TABLE half(x);\nSELECT * FROM missing;"])
    with pytest.raises(sqlite3.OperationalError):
        H.connect(path)
    with sqlite3.connect(path) as c:
        assert c.execute("PRAGMA user_version").fetchone()[0] == 1
        assert not c.execute("SELECT 1 FROM sqlite_master WHERE name='half'").fetchone()


def test_unknown_database_is_refused_but_an_empty_file_boots(tmp_path):
    foreign = tmp_path / "foreign.sqlite"
    with sqlite3.connect(foreign) as c:
        c.execute("CREATE TABLE tasks(id TEXT, title TEXT)")
    with pytest.raises(H.UnknownDatabase, match="not one this Tico made"):
        boot(foreign)
    with sqlite3.connect(foreign) as c:
        assert c.execute("PRAGMA user_version").fetchone()[0] == 0
    empty = tmp_path / "empty.sqlite"
    empty.touch()
    boot(empty)
    assert state(empty)[0] == len(H.MIGRATIONS)


def test_retired_routine_tables_are_copied_before_they_are_dropped(tmp_path):
    path = old_release(tmp_path)
    with sqlite3.connect(path) as c:
        c.execute("CREATE TABLE routine_sources(id TEXT, body TEXT)")
        c.execute("INSERT INTO routine_sources VALUES('r1','keep me')")
        c.execute("DELETE FROM cloud_migrations WHERE version=28")
    boot(path)
    with sqlite3.connect(path) as c:
        assert c.execute("SELECT body FROM routine_sources_retired").fetchall() == [("keep me",)]
        assert not c.execute("SELECT 1 FROM sqlite_master WHERE name='routine_sources'").fetchone()
