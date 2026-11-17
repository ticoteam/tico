"""Upgrade safety: what a start does with a file it did not make (a foreign one, an empty one), and the rows the journey
test seeds into the previous release before it upgrades."""

import sqlite3
from pathlib import Path

import pytest

from backend.migrations import MigrationError
from backend.tests.helpers.upgrade import boot


def test_a_foreign_database_is_refused_unchanged_but_an_empty_file_boots(tmp_path):
    foreign = tmp_path / "foreign.sqlite"
    with sqlite3.connect(foreign) as c:
        c.execute("CREATE TABLE tasks(id TEXT, title TEXT)")
    before = foreign.read_bytes()
    with pytest.raises(MigrationError, match="older than 0.6.0, or not by Tico"):
        boot(foreign)
    assert foreign.read_bytes() == before
    empty = tmp_path / "empty.sqlite"
    empty.touch()
    boot(empty)
    with sqlite3.connect(empty) as c:
        assert c.execute("SELECT 1 FROM sqlite_master WHERE name='tasks'").fetchone()


def test_the_journey_seeds_an_install_whose_starter_bot_has_its_own_id(tmp_path):
    """scripts/journey-test.sh seeds the previous release with make_upgrade_fixture.py --into. Since the cleanup a
    starter bot has an id that is not its slug, and the fixture's id=slug row would trip the slug/id trigger (an abort,
    which INSERT OR IGNORE does not skip): the install's own bot stays and the conversation and tasks still go in."""
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "make_upgrade_fixture", Path(__file__).parents[2] / "scripts" / "make_upgrade_fixture.py"
    )
    fixture = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(fixture)
    path = tmp_path / "hub.sqlite"
    boot(path)
    with sqlite3.connect(path) as c:
        if not c.execute("SELECT 1 FROM bots WHERE slug='coo'").fetchone():
            c.execute(
                "INSERT INTO bots(id,slug,display_name,state,created_at) "
                "VALUES('0199a1b2-c3d4-7e5f-8a9b-0c1d2e3f4a5b','coo','COO','active','2026-01-01T00:00:00Z')"
            )
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
