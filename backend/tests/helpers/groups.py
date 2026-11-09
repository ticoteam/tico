"""Helpers that other tests share, moved out of test_groups.py so they import no test file."""

import json
from backend.store import H, encode
from backend.tests.helpers.api import get, headers
from backend import bot_settings as BS


def send(api, method, path, body=None, token="ana-test", expected=200):
    r = api.request(method, "/api/v2/" + path, json=body, headers=headers(token))
    assert r.status_code == expected, r.text
    return r.json()


def group(api, gid, token="ana-test"):
    return next(row for row in get(api, "groups", token=token) if row["id"] == gid)


def roster_of(api):
    with api.app.state.store.read() as c:
        return json.loads(c.execute("SELECT value FROM registry_metadata WHERE key='people'").fetchone()[0])


def set_roster(api, doc):
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE registry_metadata SET value=? WHERE key='people'", (encode(doc),))


def add_bots(c, **bots):
    """{slug: (config, reports_to)}: bots straight into the database."""
    for slug, (config, reports_to) in bots.items():
        if not H.bot(c, slug):
            c.execute(
                "INSERT INTO bots(id,slug,display_name,effort,state,created_at) VALUES(?,?,?,?,'active',?)",
                (slug, slug, slug, "", H.now()),
            )
        c.execute("DELETE FROM bot_config WHERE bot=?", (slug,))
        c.execute("INSERT INTO bot_config(bot,owner_id,reports_to) VALUES(?,?,?)", (slug, "ana", reports_to))
        BS.write(c, slug, {"name": slug, "runtime": "fake", "model": "m", "host": "keeper", **config})
