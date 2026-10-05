"""Assignment and waiting-on startup changes must coexist across upgrades."""
import pytest

from backend.config import Settings
from backend.store import Store


@pytest.mark.parametrize("missing", [(58,), (59,), (58, 59)])
def test_assignment_and_waiting_on_upgrade_preserves_both_schemas(tmp_path, missing):
    registry = tmp_path / "registry"
    registry.mkdir()
    store = Store(Settings(db_path=tmp_path / "synthetic.db", registry_dir=registry))
    store.initialize(seed_market=False)
    with store.transaction() as c:
        c.execute("INSERT INTO registry_metadata VALUES('upgrade-sentinel','preserve-me')")
        if 58 in missing:
            c.execute("ALTER TABLE runners DROP COLUMN capabilities_json")
        if 59 in missing:
            c.execute("ALTER TABLE tasks DROP COLUMN waiting_on")
        for version in missing:
            c.execute("DELETE FROM cloud_migrations WHERE version=?", (version,))
    for _ in range(2):
        store.initialize(seed_market=False)
        with store.read() as c:
            assert "capabilities_json" in {row[1] for row in c.execute("PRAGMA table_info(runners)")}
            assert "waiting_on" in {row[1] for row in c.execute("PRAGMA table_info(tasks)")}
            assert c.execute("SELECT count(*) FROM cloud_migrations WHERE version IN (58,59)").fetchone()[0] == 2
            assert c.execute("SELECT value_json FROM registry_metadata WHERE key='upgrade-sentinel'").fetchone()[0] == "preserve-me"
            assert not list(c.execute("PRAGMA foreign_key_check"))
