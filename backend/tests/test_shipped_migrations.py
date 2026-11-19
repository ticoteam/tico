"""A migration that has shipped is frozen: an install recorded its checksum, and a start refuses a database whose
recorded checksum no longer matches the file (backend/migrations/__init__.py). v0.5.206 failed its upgrade check that
way after a repo-wide .js -> .ts rewrite and a reformat touched one, so the release-time check is caught here first.

fixtures/shipped_migrations.json holds the checksum of every migration file a release shipped that is still here (the
files the compatibility floor retired are gone, backend/migrations/retired.txt). Refresh it from a release tag when
one ships (files that shipped later are not covered until then):

    git ls-tree -r --name-only vX.Y.Z backend/migrations  # then sha256 of `git show vX.Y.Z:<path>` per file
"""

import json
from pathlib import Path

from backend.migrations import files, retired

SHIPPED = json.loads((Path(__file__).parent / "fixtures" / "shipped_migrations.json").read_text())


def test_no_shipped_migration_changed_or_went_missing():
    current = {migration.path.name: migration.checksum for migration in files()}
    changed = sorted(name for name, checksum in SHIPPED.items() if current.get(name) != checksum)
    assert not changed, (
        f"Shipped migrations changed or removed: {', '.join(changed)}. Installs recorded their checksums and will "
        "refuse to start: restore the released bytes (git show <tag>:backend/migrations/<file>) and make the change "
        "in a new migration."
    )


def test_a_retired_file_is_gone_and_a_shipped_one_is_not_retired():
    """A retired version is recorded on every database at the floor and never run: its file must not come back, and a
    file a later release shipped must not be listed as retired (it would never run on a database that lacks it)."""
    gone = retired()
    assert gone and not gone & {migration.version for migration in files()}
    assert not gone & {name.rsplit(".", 1)[0] for name in SHIPPED}
