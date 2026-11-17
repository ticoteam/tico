"""Finance's org-chart triggers (tico.ORG_TRIGGERS) exist after every start: a start makes any that is missing. A bot's
manager or team moves the view version Finance's caches key on."""

import sqlite3
from pathlib import Path

from backend import bot_settings as BS
from backend.config import Settings
from backend.finance.store import access as SA
from backend.finance.store import tico
from backend.store import Store

FIXTURES = Path(__file__).parent / "fixtures"
NAMES = {name for name, _, _ in tico.ORG_TRIGGERS}


def _boot(path):
    store = Store(Settings(db_path=path, registry_dir=FIXTURES / "registry"))
    store.initialize(seed_market=False)
    return store


def _trigger_names(c):
    return {row[0] for row in c.execute("SELECT name FROM sqlite_master WHERE type='trigger'")}


def test_a_start_makes_an_org_trigger_or_the_version_row_that_is_missing(tmp_path):
    """The triggers come from tico.ORG_TRIGGERS on every start, so a list change reaches every install."""
    path = tmp_path / "hub.sqlite"
    _boot(path)
    name = sorted(NAMES)[0]
    with sqlite3.connect(path) as c:
        c.execute(f'DROP TRIGGER "{name}"')
        c.execute("DELETE FROM fin_view_versions")
    store = _boot(path)
    with store.transaction() as c:
        assert NAMES <= _trigger_names(c)
        c.execute(
            "INSERT INTO bots(id,slug,display_name,state,created_at) "
            "VALUES('org-bot','org-bot','Org bot','active','2026-01-01')"
        )
        BS.write(c, "org-bot", {"model": "m", "team": "eng"})
        before = SA.view_version(c)
        BS.update(c, "org-bot", {"model": "other"})
        assert SA.view_version(c) == before  # a model is not the org chart
        BS.update(c, "org-bot", {"reports_to": "human:ana"})
        assert SA.view_version(c) == before + 1
        BS.update(c, "org-bot", {"team": "web"})
        assert SA.view_version(c) == before + 2


def test_a_new_file_has_every_org_trigger_and_a_bot_joining_moves_the_version(tmp_path):
    store = _boot(tmp_path / "new.sqlite")
    with store.transaction() as c:
        assert NAMES <= _trigger_names(c)
        c.execute(
            "INSERT INTO bots(id,slug,display_name,state,created_at) "
            "VALUES('fresh-bot','fresh-bot','Fresh bot','active','2026-01-01')"
        )
        before = SA.view_version(c)
        BS.write(c, "fresh-bot", {"reports_to": "human:ana"})
        assert SA.view_version(c) == before + 1
