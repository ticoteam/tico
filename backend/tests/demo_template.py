"""Private copies of a worker's seeded demo, including its filesystem data."""

import dataclasses
import shutil
import sqlite3
from contextlib import closing
from pathlib import Path


def copy_demo(settings, directory):
    source_dir = settings.db_path.parent
    database_files = {settings.db_path.name + suffix for suffix in ("", "-wal", "-shm")}
    shutil.copytree(source_dir, directory, ignore=lambda path, names: (
        database_files if Path(path) == source_dir else ()))
    # Backup includes committed WAL pages without copying live SQLite sidecars.
    with closing(sqlite3.connect(settings.db_path.resolve().as_uri() + "?mode=ro", uri=True)) as source:
        with closing(sqlite3.connect(directory / settings.db_path.name)) as target:
            source.backup(target)
    # Repository resources stay shared; every path into the seeded demo belongs to this copy.
    return dataclasses.replace(settings, **{
        field.name: directory / value.relative_to(source_dir)
        for field in dataclasses.fields(settings)
        if isinstance(value := getattr(settings, field.name), Path) and value.is_relative_to(source_dir)
    })
