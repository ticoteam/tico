"""A copied demo retains committed WAL data and isolates database and filesystem writes."""

import sqlite3
from contextlib import closing

from backend.config import Settings
from backend.tests.demo_template import copy_demo


def test_copies_keep_wal_rows_and_private_files(tmp_path):
    root = tmp_path / "template"
    root.mkdir()
    settings = Settings(db_path=root / "hub.sqlite", registry_dir=root / "registry", blob_dir=root / "blobs",
                        local_owner_token_file=root / "local-owner.token",
                        company_integrations_dir=root / "registry" / "integrations")
    settings.company_integrations_dir.mkdir(parents=True)
    settings.blob_dir.mkdir()
    files = [settings.registry_dir / "people.yaml", settings.blob_dir / "attachment",
             settings.local_owner_token_file, settings.company_integrations_dir / "query.sql", root / ".tico-demo"]
    for path in files:
        path.write_text("original")
    with closing(sqlite3.connect(settings.db_path)) as source:
        source.execute("PRAGMA journal_mode=WAL")
        source.execute("PRAGMA wal_autocheckpoint=0")
        source.execute("CREATE TABLE sample(value TEXT)")
        source.execute("INSERT INTO sample VALUES ('original')")
        source.commit()
        assert settings.db_path.with_name("hub.sqlite-wal").stat().st_size > 0

        first = copy_demo(settings, tmp_path / "first")
        with closing(sqlite3.connect(first.db_path)) as changed:
            assert changed.execute("SELECT value FROM sample").fetchall() == [("original",)]
            changed.execute("UPDATE sample SET value='changed'")
            changed.commit()
        for path in files:
            (first.db_path.parent / path.relative_to(root)).write_text("changed")

        second = copy_demo(settings, tmp_path / "second")
        with closing(sqlite3.connect(second.db_path)) as untouched:
            assert untouched.execute("SELECT value FROM sample").fetchall() == [("original",)]
        assert source.execute("SELECT value FROM sample").fetchall() == [("original",)]
        for path in files:
            assert path.read_text() == "original"
            assert (second.db_path.parent / path.relative_to(root)).read_text() == "original"
        for copied in (first, second):
            for name in ("db_path", "registry_dir", "blob_dir", "local_owner_token_file", "company_integrations_dir"):
                assert getattr(copied, name) == copied.db_path.parent / getattr(settings, name).relative_to(root)
            assert copied.catalog_dir == settings.catalog_dir
