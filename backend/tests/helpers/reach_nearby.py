"""The org chart and reach default the reach tests share (test_reach_nearby.py, test_flow_ping.py): cpo reports to
Ben; product-design, finance and doc-updater around it; ops two levels down. Acme is the fictional company."""

import json

from backend import bot_settings as BS
from backend.store import encode
from backend.tests.helpers.task_caps import as_bot
import pytest


@pytest.fixture(autouse=True)
def nearby_default(monkeypatch):
    """conftest.py starts other tests' bots open; these test the shipped default."""
    monkeypatch.delenv("TICO_REACH_DEFAULT", raising=False)


def chart(api):
    with api.app.state.store.transaction() as c:
        roster = json.loads(c.execute("SELECT value FROM registry_metadata WHERE key='people'").fetchone()[0])
        roster["org_groups"] = {"product": {"name": "Product"}}
        c.execute("UPDATE registry_metadata SET value=? WHERE key='people'", (encode(roster),))
        for bot, boss, team in (
            ("cpo", "human:ben", "product"),
            ("product-design", "cpo", "product"),
            ("finance", "cpo", ""),
            ("ops", "product-design", ""),
            ("doc-updater", None, "product"),
        ):
            c.execute("UPDATE bot_config SET reports_to=?, team=? WHERE bot=?", (boss, team, bot))
            BS.update(c, bot, {"team": team})
        c.execute("INSERT INTO bots(id, slug, display_name, state) VALUES('scout', 'scout', 'Scout', 'active')")
        c.execute("INSERT INTO bot_config(bot, team, owner_id) VALUES('scout', '', 'ana')")
    as_bot(api, "product-design", "pd-test")
    as_bot(api, "doc-updater", "doc-test")
