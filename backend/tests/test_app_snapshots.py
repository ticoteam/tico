"""Kept app databases (backend/tests/app_snapshots.py): two apps share one only when nothing initialize reads differs."""

import sqlite3
from contextlib import closing

import pytest

from backend.auth import Identity
from backend.config import Settings
from backend.tests.app_snapshots import fingerprint, holds


def settings(tmp_path, owner="ana@acme.example", registry="owner: ana@acme.example\n"):
    tmp_path.mkdir(parents=True, exist_ok=True)
    (tmp_path / "registry").mkdir(exist_ok=True)
    (tmp_path / "registry" / "hub-access.yaml").write_text(registry)
    return Settings(
        db_path=tmp_path / "hub.db",
        registry_dir=tmp_path / "registry",
        owner_email=owner,
        test_identities={"ana-test": Identity("human:ana", "owner", owner)},
    )


def test_the_same_app_in_another_test_directory_has_the_same_fingerprint_and_any_difference_another(tmp_path):
    patched = pytest.MonkeyPatch()
    same = fingerprint(settings(tmp_path / "a"), patched, None)
    assert fingerprint(settings(tmp_path / "b"), patched, None) == same
    assert fingerprint(settings(tmp_path / "c", owner="ben@acme.example"), patched, None) != same
    assert fingerprint(settings(tmp_path / "d", registry="owner: ben@acme.example\n"), patched, None) != same
    import backend.hubdb

    patched.setattr(backend.hubdb, "TITLE_LINT", "warn")
    try:
        assert fingerprint(settings(tmp_path / "e"), patched, None) != same
        assert fingerprint(settings(tmp_path / "e"), patched, (backend.hubdb, "TITLE_LINT")) == same
    finally:
        patched.undo()


def test_a_database_that_holds_its_own_directory_is_found(tmp_path):
    path = tmp_path / "hub.db"
    with closing(sqlite3.connect(path)) as c:
        c.execute("CREATE TABLE files(path TEXT)")
        c.execute("INSERT INTO files VALUES('elsewhere')")
        c.commit()
    assert not holds(path, str(tmp_path))
    with closing(sqlite3.connect(path)) as c:
        c.execute("INSERT INTO files VALUES(?)", (str(tmp_path / "catalog"),))
        c.commit()
    assert holds(path, str(tmp_path))
