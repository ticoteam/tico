"""A migration that has shipped is frozen: an install recorded its checksum, and a start refuses a database whose
recorded checksum no longer matches the file (backend/migrations/__init__.py). v0.5.206 failed its upgrade check that
way after a repo-wide .js -> .ts rewrite and a reformat touched one, so the release-time check is caught here first.

fixtures/shipped_migrations.json holds the checksum of every migration file a release shipped. Refresh it from a
release tag when one ships (files that shipped later are not covered until then):

    git ls-tree -r --name-only vX.Y.Z backend/migrations  # then sha256 of `git show vX.Y.Z:<path>` per file
"""

import json
from pathlib import Path

from backend.migrations import files

SHIPPED = json.loads((Path(__file__).parent / "fixtures" / "shipped_migrations.json").read_text())


def test_no_shipped_migration_changed_or_went_missing():
    current = {migration.path.name: migration.checksum for migration in files()}
    changed = sorted(name for name, checksum in SHIPPED.items() if current.get(name) != checksum)
    assert not changed, (
        f"Shipped migrations changed or removed: {', '.join(changed)}. Installs recorded their checksums and will "
        "refuse to start: restore the released bytes (git show <tag>:backend/migrations/<file>) and make the change "
        "in a new migration."
    )
