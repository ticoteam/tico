"""Helpers that other tests share, moved out of test_new_bot_flows.py so they import no test file."""

from backend.store import H
from backend.tests.helpers.api import headers
from backend import bot_settings as BS


def create_bot(api, slug, token="ana-test", **fields):
    body = {
        "slug": slug,
        "display_name": slug.replace("-", " ").title(),
        "description": "Answers sales questions",
        "status": "active",
        **fields,
    }
    r = api.post("/api/v2/bots", json=body, headers=headers(token))
    assert r.status_code == 200, r.text
    with api.app.state.store.transaction() as c:
        bot_id = H.resolve_bot(c, slug)["id"]
        # It runs on the test runtime the fixture's bots run on, so the test computer can claim its work.
        config = BS.read(c, bot_id)
        config.update({"runtime": "fake", "harness": None, "model": "", "model_managed_by": None})
        BS.write(c, bot_id, config)
    return bot_id
