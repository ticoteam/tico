"""Helpers that other tests share, moved out of test_strategy_bot_chat.py so they import no test file."""

import uuid
from backend.store import H, digest
from backend.tests.helpers.api import assign, post, ready, runner
from backend.tests.helpers.assistant import say
from backend import bot_settings as BS


def strategy_bot(api):
    """The Strategy Bot every set-up install has."""
    with api.app.state.store.transaction() as c:
        c.execute(
            "INSERT INTO bots(id,slug,display_name,effort,state,created_at) "
            "VALUES('goal-manager','goal-manager','Strategy Bot','','active',?)",
            (H.now(),),
        )
        c.execute("INSERT INTO bot_config(bot,team,owner_id) VALUES('goal-manager',NULL,'ana')")
        BS.write(c, 'goal-manager', {"runtime": "fake", "host": "keeper"})


def personal(api, pid):
    """A personal API token: its role follows the person's email, so it follows a transfer of ownership."""
    secret = "tok-" + pid + "-" + uuid.uuid4().hex
    with api.app.state.store.transaction() as c:
        c.execute(
            "INSERT INTO human_tokens(id,human,label,token_hash,created_at,created_by) VALUES(?,?,?,?,?,?)",
            (uuid.uuid4().hex, pid, "test", digest(secret), H.now(), "test"),
        )
    return secret


def turn(api, bot, person, text):
    """The bot's runner turn for what this person said in their own chat."""
    r = runner(api)
    assign(api, r, bot)
    ready(api, r, [bot])
    assert say(api, text, person)
    return r, post(api, "jobs/claim", {}, token=r["token"])["attempt"]
