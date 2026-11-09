"""Helpers that other tests share, moved out of test_botops_repositories.py so they import no test file."""

import json
import pytest
from backend.tests.helpers.api import get
from backend.store import H
from backend import bot_settings as BS


@pytest.fixture
def repos(api):
    """Acme/product and Acme/docs ticked in Settings; ops has write on product, BotOps read on docs."""
    api.app.state.store.settings.github_owner = "Acme"
    with api.app.state.store.transaction() as c:
        for name in ("Acme/product", "Acme/docs"):
            c.execute(
                "INSERT INTO repositories(id,full_name,enabled,updated_at) VALUES(?,?,1,?)", (H.new_id(), name, H.now())
            )
        for bot, name, level in (("ops", "Acme/product", "write"), ("botops", "Acme/docs", "read")):
            config = json.loads(
                c.execute(f"SELECT {BS.CONFIG} AS config FROM bot_config bc {BS.JOIN} WHERE bot=?", (bot,)).fetchone()[
                    0
                ]
                or "{}"
            )
            BS.write(c, bot, {**config, 'repo_access_mode': 'chosen'})
            c.execute("INSERT INTO bot_repo_access VALUES(?,?,?)", (bot, name, level))


def effective(api, bot="ops"):
    return get(api, f"bots/{bot}/repositories", "ana-test")["effective"]


FLAG = {"full_name": "Acme/product", "access": "write", "run_workflows": True}
