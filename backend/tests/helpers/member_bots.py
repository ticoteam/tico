"""Helpers that other tests share, moved out of test_member_bots.py so they import no test file."""

import pytest
from backend.tests.helpers.api import assign, claim, headers, post, ready, runner
from backend.tests.test_api import api
from backend import bot_settings as BS


def call(api, method, path, token, body=None):
    kwargs = {"headers": headers(token)}
    if body is not None:
        kwargs["json"] = body
    return getattr(api, method)("/api/v2/" + path, **kwargs)


def close_computer(api, machine):
    """An admin closes a computer to members' bots (a new one takes them by default)."""
    assert (
        call(api, "post", f"runners/{machine['runner_id']}/member-bots", "ben-test", {"accepts": False}).status_code
        == 200
    )
    return machine


@pytest.fixture
def botops(api):
    with api.app.state.store.transaction() as c:
        c.execute("INSERT INTO bots(id,slug,display_name,state) VALUES('botops','botops','BotOps','active')")
        c.execute("INSERT INTO bot_config(bot,owner_id) VALUES('botops','ana')")
        BS.write(c, 'botops', {'name': 'botops', 'runtime': 'fake', 'status': 'active'})
    machine = runner(api)
    assign(api, machine, "botops")
    ready(api, machine, ["botops"])
    return machine


def turn(api, machine, person="cara-test", text="Build me a bot"):
    """A person chats with BotOps; its runner claims that message: the attempt whose token BotOps acts with."""
    post(api, "chat/botops", {"text": text}, token=person)
    return claim(api, machine, "botops")


def finish(api, machine, attempt):
    post(api, f"attempts/{attempt['id']}/started", {"thread_id": "t"}, machine["token"])
    post(api, f"attempts/{attempt['id']}/complete", {"outcome": "completed", "last_seq": 0}, machine["token"])


def register(api, attempt, slug, **fields):
    return call(
        api,
        "post",
        "bots/register",
        attempt["token"],
        {"slug": slug, "display_name": slug.title(), "on_behalf_of": "turn", **fields},
    )
