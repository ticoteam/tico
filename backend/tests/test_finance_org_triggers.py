"""Finance's org-chart triggers exist after every start, on a new file and on one upgraded past migration 0070 (the
bot_settings move). Migration 0070 drops the trigger that read bot_config.config, and the bot_settings triggers did not
exist before the move, so an install whose Finance migrations ran earlier had neither until the start made them. A bot's
manager or team moves the view version Finance's caches key on, on both."""
import json
import sqlite3
from pathlib import Path

from backend import bot_settings as BS
from backend.config import Settings
from backend.finance.store import access as SA
from backend.finance.store import tico
from backend.store import Store
from backend.tests.test_migrations import restore_config

FIXTURES = Path(__file__).parent / "fixtures"
NAMES = {name for name, _, _ in tico.ORG_TRIGGERS}
# The org trigger on a bot's manager, and its definition before the move: it named bot_config.config.
BOT_UPDATE = next(name for name, when, _ in tico.ORG_TRIGGERS if "ON bot_config" in when and "reports_to" in when)
OLD_BOT_UPDATE = (f"CREATE TRIGGER {BOT_UPDATE} AFTER UPDATE OF reports_to, config ON bot_config "
                  "WHEN OLD.reports_to IS NOT NEW.reports_to BEGIN SELECT 1; END")


def _boot(path):
    store = Store(Settings(db_path=path, registry_dir=FIXTURES / "registry"))
    store.initialize(seed_market=False)
    return store


def _trigger_names(c):
    return {row[0] for row in c.execute("SELECT name FROM sqlite_master WHERE type='trigger'")}


def _upgraded_before_the_move(path):
    """A database as Finance's migrations left it before 0070: bot_config.config holds each declaration, the trigger on
    a bot's manager names that column, bot_settings does not exist yet, the view-version row is absent, and 0070 is
    pending again."""
    _boot(path)
    with sqlite3.connect(path) as c:
        restore_config(c)
        for (name,) in c.execute("SELECT name FROM sqlite_master WHERE type='trigger' AND tbl_name='bot_settings'").fetchall():
            c.execute(f'DROP TRIGGER "{name}"')
        c.execute("DROP TABLE bot_settings")
        c.execute(f"DROP TRIGGER {BOT_UPDATE}")
        c.execute(OLD_BOT_UPDATE)
        c.execute("DELETE FROM fin_view_versions")
        c.execute("INSERT INTO bots(id,slug,display_name,state,created_at) "
                  "VALUES('org-bot','org-bot','Org bot','active','2026-01-01')")
        c.execute("INSERT INTO bot_config(bot,owner_id,config) VALUES('org-bot','ana',?)",
                  (json.dumps({"model": "m", "team": "eng"}),))
        c.execute("DELETE FROM schema_migrations WHERE version >= '0070_bot_settings' AND version NOT LIKE 'legacy_%'")


def test_an_upgraded_install_gets_every_org_trigger_on_its_next_start(tmp_path):
    path = tmp_path / "hub.sqlite"
    _upgraded_before_the_move(path)
    store = _boot(path)
    with store.transaction() as c:
        assert NAMES <= _trigger_names(c)
        assert "config" not in {row[1] for row in c.execute("PRAGMA table_info(bot_config)")}
        assert BS.read(c, "org-bot")["model"] == "m" and BS.read(c, "org-bot")["team"] == "eng"
        before = SA.view_version(c)
        BS.update(c, "org-bot", {"model": "other"})
        assert SA.view_version(c) == before                       # a model is not the org chart
        BS.update(c, "org-bot", {"reports_to": "human:ana"})
        assert SA.view_version(c) == before + 1
        BS.update(c, "org-bot", {"team": "web"})
        assert SA.view_version(c) == before + 2
    with _boot(path).read() as c:                                 # a second start makes nothing new
        assert NAMES <= _trigger_names(c)


def test_a_new_file_has_every_org_trigger_and_a_bot_joining_moves_the_version(tmp_path):
    store = _boot(tmp_path / "new.sqlite")
    with store.transaction() as c:
        assert NAMES <= _trigger_names(c)
        c.execute("INSERT INTO bots(id,slug,display_name,state,created_at) "
                  "VALUES('fresh-bot','fresh-bot','Fresh bot','active','2026-01-01')")
        before = SA.view_version(c)
        BS.write(c, "fresh-bot", {"reports_to": "human:ana"})
        assert SA.view_version(c) == before + 1
