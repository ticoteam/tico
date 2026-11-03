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
    # Below the bridge (test_migrations), so this is the legacy import path: adopt_legacy, as manage.migrate_legacy boots.
    path = old_release(tmp_path)
    boot(path, adopt_legacy=True)
    first = state(path)
    assert first[0] == len(H.MIGRATIONS)
    boot(path, adopt_legacy=True)
    assert state(path) == first
    with sqlite3.connect(path) as c:
        assert [r[0] for r in c.execute("SELECT slug FROM bots ORDER BY slug")] == ["cmo", "coo", "seo"]
        assert c.execute("SELECT count(*) FROM bots").fetchone()[0] == 3
        assert c.execute("SELECT count(*) FROM tasks").fetchone()[0] == 3
        assert c.execute("SELECT count(*) FROM messages").fetchone()[0] == 2


def test_goal_id_already_present_with_version_behind_still_boots(tmp_path):
    path = old_release(tmp_path)
    with sqlite3.connect(path) as c:
        assert "goal_id" in {r[1] for r in c.execute("PRAGMA table_info(tasks)")}
        c.execute("PRAGMA user_version=5")   # the goals migration (and everything after) not recorded
    boot(path, adopt_legacy=True)
    assert state(path)[0] == len(H.MIGRATIONS)


@pytest.mark.parametrize("version", [38, 39])
def test_purpose_migration_backfills_unlabelled_turns_and_keeps_caller_labels(tmp_path, version):
    conn = H.connect(tmp_path / "purpose.sqlite")
    try:
        # A file may have the column before recording the version bump.
        conn.execute(f"PRAGMA user_version={version}")
        conn.executemany("INSERT INTO turns(id,bot,started_at,trigger_kind,purpose) VALUES(?,?,'2026-01-01',?,?)",
                         [("builder", "pr-reviewer", "pr_checks", None),
                          ("reviewer", "pr-reviewer-sol", "status_or_relation", None),
                          ("unknown", "ops", None, None),
                          ("named", "pr-reviewer", "pr_checks", "prepare release")])
        H.migrate(conn)
        expected = {"builder": "handle PR checks", "reviewer": "review PR", "unknown": "unknown",
                    "named": "prepare release"}
        assert dict(conn.execute("SELECT id,purpose FROM turns")) == expected
        assert conn.execute("PRAGMA user_version").fetchone()[0] == len(H.MIGRATIONS)
        H.migrate(conn)
        assert dict(conn.execute("SELECT id,purpose FROM turns")) == expected
    finally:
        conn.close()


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
    boot(path, adopt_legacy=True)
    with sqlite3.connect(path) as c:
        assert c.execute("SELECT body FROM routine_sources_retired").fetchall() == [("keep me",)]
        assert not c.execute("SELECT 1 FROM sqlite_master WHERE name='routine_sources'").fetchone()


def test_the_journey_seeds_an_install_whose_starter_bot_has_its_own_id(tmp_path):
    """scripts/journey-test.sh seeds the previous release with make_upgrade_fixture.py --into. Since the cleanup a
    starter bot has an id that is not its slug, and the fixture's id=slug row would trip the slug/id trigger (an abort,
    which INSERT OR IGNORE does not skip): the install's own bot stays and the conversation and tasks still go in."""
    import importlib.util
    spec = importlib.util.spec_from_file_location("make_upgrade_fixture",
                                                  Path(__file__).parents[2] / "scripts" / "make_upgrade_fixture.py")
    fixture = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(fixture)
    path = tmp_path / "hub.sqlite"
    boot(path)
    with sqlite3.connect(path) as c:
        if not c.execute("SELECT 1 FROM bots WHERE slug='coo'").fetchone():
            c.execute("INSERT INTO bots(id,slug,display_name,state,created_at) "
                      "VALUES('0199a1b2-c3d4-7e5f-8a9b-0c1d2e3f4a5b','coo','COO','active','2026-01-01T00:00:00Z')")
        own = c.execute("SELECT id FROM bots WHERE slug='coo'").fetchone()[0]
    assert own != "coo"
    fixture.seed_into(str(path))
    with sqlite3.connect(path) as c:
        assert c.execute("SELECT id FROM bots WHERE slug='coo'").fetchone()[0] == own
        assert {r[0] for r in c.execute("SELECT slug FROM bots")} >= {"coo", "cmo", "seo"}
        assert c.execute("SELECT count(*) FROM messages WHERE conversation_id='c1'").fetchone()[0] == 2
        assert c.execute("SELECT count(*) FROM tasks WHERE id IN ('t1','t2','t3')").fetchone()[0] == 3
        # the seeded rows name the install's bot, so the jobs its messages queue point at a bot that exists
        assert {r[0] for r in c.execute("SELECT owner_id FROM tasks WHERE id IN ('t1','t2','t3')")} == {f"bot:{own}"}
        assert c.execute("PRAGMA foreign_key_check").fetchall() == []
