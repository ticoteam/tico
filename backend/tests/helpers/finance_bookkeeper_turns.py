"""Shared by the tests that import it, moved out of test_finance_bookkeeper_turns.py so they import no test file."""

from pathlib import Path
from backend import bot_settings as BS
from backend import bookkeeper as BK
from backend.finance import actions as FA
from backend.store import H
from backend.tests.helpers.api import assign, claim, get, post, ready, runner  # noqa: F401
from backend.tests.helpers.finance_fixtures import MERCURY, OWNER, FakeMercury, account, act, connect, sync_once  # noqa: F401
from backend.tests.helpers.mcp import call, rpc


CONFIG = {"template": "bookkeeper", "name": "bookkeeper", "runtime": "fake", "status": "active"}


def hire(api):
    """The Bookkeeper as the product makes it once Finance is set up: its template, its default rights (book, ap, ar,
    pay, cards: backend/bookkeeper.py grant_defaults), and the Everything view the owner is asked to grant. Returns a
    run token for the bot, the credential its hub calls carry."""
    store = api.app.state.store
    with store.transaction() as c:
        assert FA.ensure_setup(c, store.settings.owner_email)
        c.execute(
            "INSERT INTO bots(id,slug,display_name,state) VALUES('bookkeeper','bookkeeper','Bookkeeper','active')"
        )
        c.execute("INSERT INTO bot_config(bot,owner_id) VALUES('bookkeeper','ana')")
        BS.write(c, "bookkeeper", CONFIG)
        assert BK.grant_defaults(c)
        granted = act(c, OWNER, "view_grant", {"subject": BK.ACTOR, "what": "everything"})
        assert granted.status == "committed", granted
    machine = runner(api)  # enrolling a computer can place the bot already: take its generation
    with store.read() as c:
        placed = c.execute("SELECT runner_id, generation FROM assignments WHERE bot=?", (BK.SLUG,)).fetchone()
    if not placed or placed["runner_id"] != machine["runner_id"]:
        assign(api, machine, BK.SLUG, generation=placed["generation"] if placed else 0)
    ready(api, machine, [BK.SLUG])
    post(api, "chat/" + BK.SLUG, {"text": "Books check: work the queue."})
    return claim(api, machine, BK.SLUG)["token"]


def mercury(api):
    """Acme's Mercury connected as the owner, with a customer deposit and a wire fee in the feed, synced once."""
    store = api.app.state.store
    fake = FakeMercury()
    connect(store, fake)
    fake.txns["txn-dep"] = dict(MERCURY["txn_deposit"])
    fake.txns["txn-fee"] = dict(MERCURY["txn_bank_fee"])
    sync_once(store, fake)
    return fake


def chart(api, code):
    """A chart account's id, read from the books (no hub tool lists the chart: see test_gap_the_bot_can_list_accounts)."""
    with api.app.state.store.read() as c:
        return account(c, code)


def ask(api, token, kind, payload, op, **extra):
    """The bot asks for one Finance action, its operation id as the action's key: (tool error flag, result)."""
    return call(api, "hub_finance_act", {"kind": kind, "payload": payload, "operation_id": op, **extra}, token=token)


def _library_file(api, fid, bot):
    """A Library file of `bot` (bot-wide scope, no versions): enough for the file check, which reads the row."""
    now = H.now()
    with api.app.state.store.transaction() as c:
        if c.execute("SELECT 1 FROM bots WHERE id=?", (bot,)).fetchone() is None:
            c.execute("INSERT INTO bots(id,slug,display_name,state) VALUES(?,?,?,'active')", (bot, bot, bot.title()))
        c.execute(
            "INSERT INTO bot_files(id,bot_id,scope,identity,title,kind,locator,first_activity_at,last_activity_at) "
            "VALUES(?,?,'bot',?,'receipt.pdf','file','',?,?)",
            (fid, bot, fid, now, now),
        )
