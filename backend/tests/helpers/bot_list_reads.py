"""Helpers that other tests share, moved out of test_bot_list_reads.py so they import no test file."""

from backend.store import H
from backend import bot_settings as BS


BASE = ("coo", "ops", "cpo", "product-design", "finance", "inbox", "doc-updater")


def add_bots(api, extra):
    with api.app.state.store.transaction() as c:
        bots = {slug: {"name": slug, "runtime": "fake", "status": "active"} for slug in (*BASE, *extra)}
        H.sync_registry(c, bots, {"people": [{"id": p, "email": p + "@acme.example"} for p in ("ana", "ben", "cara")]})
        for slug, config in extra.items():
            c.execute('INSERT INTO bot_config(bot,team,owner_id) VALUES(?,?,?)', (slug, None, 'ana'))
            BS.write(c, slug, {**bots[slug], **config})
