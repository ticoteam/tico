"""Helpers that other tests share, moved out of test_market.py so they import no test file."""

from pathlib import Path
from backend import market as M
from backend.blobs import Blobs
from backend.store import H
from backend import bot_settings as BS


FIXTURE_REGISTRY = Path(__file__).parents[1] / "fixtures" / "registry"


def _seed(api, tmp_path):
    store = api.app.state.store
    with store.transaction() as c:
        M.seed(c, {}, Blobs(store.settings), document=M.load_snapshot(FIXTURE_REGISTRY))
        if not H.bot(c, "listening"):
            c.execute(
                "INSERT INTO bots (id, slug, display_name, effort, state, created_at) VALUES (?,?,?,?,?,?)",
                ("listening", "listening", "Listening", "high", "active", H.now()),
            )
        if not c.execute("SELECT 1 FROM bot_config WHERE bot='listening'").fetchone():
            c.execute(
                "INSERT INTO bot_config (bot, team, owner_id, description, reports_to, repo) VALUES (?,?,?,?,?,?)",
                ("listening", "marketing", "ana", "Listening", "cmo", "emp-listening"),
            )
            BS.write(c, "listening", {"runtime": "grok", "model": "grok-4.6", "host": "keeper"})


def _librarian(api):
    with api.app.state.store.transaction() as c:
        if not H.bot(c, "librarian"):
            c.execute(
                "INSERT INTO bots(id,slug,display_name,effort,state,created_at) "
                "VALUES('librarian','librarian','Librarian','','active',?)",
                (H.now(),),
            )
            c.execute("INSERT INTO bot_config(bot,team,owner_id) VALUES('librarian',NULL,'ana')")
            BS.write(c, 'librarian', {"runtime": "fake", "host": "keeper"})
