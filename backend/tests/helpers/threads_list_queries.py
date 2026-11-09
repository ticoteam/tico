"""Helpers that other tests share, moved out of test_threads_list_queries.py so they import no test file."""

from backend import bot_settings as BS
from backend import hubdb


def add_bots(api, start, count):
    """`count` more bots Ana owns, as a company has them: each gets a DM with Ana on her sidebar (bot_dm_unread)."""
    with api.app.state.store.transaction() as c:
        bots = {
            f"desk-{n}": {"name": f"desk-{n}", "runtime": "fake", "status": "active"}
            for n in range(start, start + count)
        }
        hubdb.sync_registry(c, bots, {"people": []})
        for slug, config in bots.items():
            c.execute("INSERT INTO bot_config(bot,team,owner_id) VALUES(?,?,?)", (slug, None, "ana"))
            BS.write(c, slug, config)
    return [f"bot:{slug}" for slug in bots]
