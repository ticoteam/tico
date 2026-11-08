"""Helpers that other tests share, moved out of test_upgrade.py so they import no test file."""

from pathlib import Path
from backend.config import Settings
from backend.store import Store


FIXTURES = Path(__file__).parents[1] / "fixtures"


def boot(path, **kw):
    Store(Settings(db_path=path, registry_dir=FIXTURES / "registry")).initialize(seed_market=False, **kw)
