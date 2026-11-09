"""Helpers that other tests share, moved out of test_org_chart.py so they import no test file."""

from backend.store import H, encode


def roster(api, people):
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE registry_metadata SET value=? WHERE key='people'", (encode({"people": people}),))


def revision(api, bot):
    with api.app.state.store.read() as c:
        bot = (H.resolve_bot(c, bot) or {}).get("id", bot)
        return c.execute("SELECT revision FROM bot_config WHERE bot=?", (bot,)).fetchone()[0]
