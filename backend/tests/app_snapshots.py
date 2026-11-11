"""An app a test builds on a new database starts from a copy of the same app's database made earlier in this worker.

A new hub database takes about 7,700 schema statements to migrate and initialize (1.2 s of CPU, 2-3 s on a CI vCPU), and
the helper fixtures (tasks_board, onboarding, github_app and others) built one per test: that was most of a 3.4 s median
test. conftest.py apps_start_from_snapshot puts `snapshot_store` in place of backend.app's Store for each test.

When create_app opens a file that does not exist yet, the app's fingerprint is looked up:
- seen before in this worker: the file starts as a copy of that app's database right after its first initialize, and
  startup runs initialize on it again, as the `api` fixture's copies do;
- not seen: the app migrates the empty file as always, and after initialize its database is kept for the next one.

The fingerprint is everything initialize reads that a test can vary: the Settings with the test's directory written as
`<tmp>`, the contents of files the test wrote there (registry, catalog, token), TICO_ environment variables, each
attribute or item the test monkeypatched (value, or a function's name), and today's date (time_machine moves it). A
database whose rows hold the test's directory is never kept, and a demo build (its ids come from a counter) neither
keeps nor takes one. A file that exists (an old schema, a copied hub) and a
Store a test builds itself (the migration tests) are never touched; @pytest.mark.fresh_database turns it off for a test.
"""

import dataclasses
import hashlib
import os
import re
import sqlite3
import uuid
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

PLAIN = (str, int, float, bool, type(None), bytes)


def describe(value):
    """A stable description of a patched or configured value: equal for equal plain values and for the same function."""
    if isinstance(value, PLAIN):
        return repr(value)
    if isinstance(value, Path):
        return f"Path({value})"
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        fields = [(field.name, describe(getattr(value, field.name))) for field in dataclasses.fields(value)]
        return type(value).__qualname__ + repr(fields)
    if isinstance(value, (tuple, list, frozenset, set)):
        items = [describe(item) for item in value]
        return type(value).__name__ + repr(sorted(items) if isinstance(value, (set, frozenset)) else items)
    if isinstance(value, dict):
        return "dict" + repr(sorted((describe(k), describe(v)) for k, v in value.items()))
    name = getattr(value, "__qualname__", None) or type(value).__qualname__
    return f"{getattr(value, '__module__', type(value).__module__)}.{name}"


def file_digest(path):
    """A digest of a file, or of every file under a directory, by relative name and content."""
    digest = hashlib.sha256()
    files = sorted(p for p in path.rglob("*") if p.is_file()) if path.is_dir() else [path]
    for file in files:
        digest.update(str(file.relative_to(path) if path.is_dir() else file.name).encode() + b"\0")
        digest.update(file.read_bytes() + b"\0")
    return digest.hexdigest()


def fingerprint(settings, monkeypatch, own):
    """What makes two new apps' databases equal right after initialize (see the module docstring)."""
    base = str(Path(settings.db_path).parent)
    parts = [datetime.now(timezone.utc).date().isoformat()]
    for field in dataclasses.fields(settings):
        value = getattr(settings, field.name)
        if isinstance(value, Path) and str(value).startswith(base) and value.exists() and field.name != "db_path":
            parts.append(f"{field.name}=file:{file_digest(value)}")
        parts.append(f"{field.name}={describe(value).replace(base, '<tmp>')}")
    parts += sorted(f"env {k}={v}" for k, v in os.environ.items() if k.startswith("TICO_"))
    for target, name, _old in getattr(monkeypatch, "_setattr", []):
        if (target, name) != own:
            owner = getattr(target, "__name__", type(target).__qualname__)
            parts.append(f"attr {owner}.{name}={describe(getattr(target, name, None))}")
    for target, name, _old in getattr(monkeypatch, "_setitem", []):
        if target is not os.environ:  # the environment is in already, whole
            parts.append(f"item {name}={describe(target.get(name))}")
    return hashlib.sha256("\n".join(parts).replace(base, "<tmp>").encode()).hexdigest()


def holds(path, text):
    """Whether any row of the database at `path` mentions `text` (a test's own directory)."""
    with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)) as c:
        for (table,) in c.execute("SELECT name FROM sqlite_master WHERE type='table'"):
            if re.search("_fts|^sqlite_", table):
                continue
            quoted = '"' + table.replace('"', '""') + '"'
            if any(text in repr(row) for row in c.execute(f"SELECT * FROM {quoted}")):
                return True
    return False


def deterministic(settings):
    """A demo build makes its ids from a counter (backend/demo_seed.py swaps uuid4 and hubdb.new_id while it seeds): a
    copy of an earlier build's rows would hold the ids it is about to make again."""
    return getattr(settings, "demo", False) or getattr(uuid.uuid4, "__module__", "uuid") != "uuid"


def copy(source, target):
    with closing(sqlite3.connect(source.as_uri() + "?mode=ro", uri=True)) as a, closing(sqlite3.connect(target)) as b:
        a.backup(b)


def snapshot_store(store_class, kept, folder, monkeypatch, own):
    """backend.app's Store for one test. `kept` ({fingerprint: snapshot path, or None when it may not be kept}) and
    `folder` live for the worker; `own` is the (module, "Store") patch itself, left out of the fingerprint."""

    class SnapshotStore(store_class):
        def __init__(self, settings, *args, **kwargs):
            path = Path(settings.db_path)
            self._keep_as = None
            if not path.exists() and not deterministic(settings):
                key = fingerprint(settings, monkeypatch, own)
                if kept.get(key):
                    path.parent.mkdir(parents=True, exist_ok=True)
                    copy(kept[key], path)
                elif key not in kept:
                    self._keep_as = key
            super().__init__(settings, *args, **kwargs)

        def initialize(self, *args, **kwargs):
            result = super().initialize(*args, **kwargs)
            key, self._keep_as = self._keep_as, None
            if key is not None and not args and not kwargs:
                path = Path(self.settings.db_path)
                if holds(path, str(path.parent)):
                    kept[key] = None
                else:
                    snapshot = folder / f"{key[:16]}.db"
                    copy(path, snapshot)
                    kept[key] = snapshot
            return result

    SnapshotStore.__name__ = SnapshotStore.__qualname__ = store_class.__name__
    return SnapshotStore
