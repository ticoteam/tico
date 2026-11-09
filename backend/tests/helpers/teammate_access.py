"""Helpers that other tests share, moved out of test_teammate_access.py so they import no test file."""

import pytest
from backend.auth import Identity
from backend.store import H, encode
from backend.tests.helpers.api import headers, post
from backend.tests.helpers.bot_access import bot


EVERYONE = {"everyone": True}


NOBODY = {"everyone": False}


@pytest.fixture
def acme(api):
    store = api.app.state.store
    store.settings.test_identities["dee-test"] = Identity("human:dee", "human", "dee@acme.example")
    with store.transaction() as c:
        people = [
            {"id": "ana", "email": "ana@acme.example", "primary_for": ["*"]},
            {"id": "ben", "email": "ben@acme.example"},
            {"id": "cara", "email": "cara@acme.example", "reports_to": "dee"},
            {"id": "dee", "email": "dee@acme.example"},
            {"id": "eve", "email": "eve@acme.example"},
            {"id": "fay", "email": "fay@acme.example"},
        ]
        c.execute("UPDATE registry_metadata SET value=? WHERE key='people'", (encode({"people": people}),))
        H.sync_registry(c, {}, {"people": people})
    bot(api, "counsel")
    bot(api, "analyst")
    post(api, "groups", {"name": "Legal", "add": {"people": ["cara"]}})
    post(api, "groups", {"name": "Contracts", "parent": "legal", "add": {"people": ["fay"], "bots": ["counsel"]}})
    post(api, "groups", {"name": "Sales", "add": {"people": ["eve"]}})
    tokens = {p: p + "-test" for p in ("ana", "ben", "cara", "dee", "eve", "fay")}
    tokens["counsel"] = post(api, "bots/counsel/agent-credential", {})["token"]
    return api, tokens


def who(api, name):
    if name in ("counsel", "analyst"):
        with api.app.state.store.read() as c:
            return Identity("bot:" + H.resolve_bot(c, name)["id"], "bot")
    return api.app.state.store.settings.test_identities[name + "-test"]


def set_access(api, path, token="ana-test", expected=200, **levels_):
    current = api.get(f"/api/v2/{path}/access", headers=headers(token))
    revision = current.json()["revision"] if current.status_code == 200 else 0
    body = {"see": EVERYONE, "read": EVERYONE, "write": EVERYONE, "revision": revision, **levels_}
    r = api.put(f"/api/v2/{path}/access", json=body, headers=headers(token))
    assert r.status_code == expected, r.text
    return r.json()


def sql_column(api, sql, token):
    r = api.post("/api/v2/sql", json={"sql": sql}, headers=headers(token))
    assert r.status_code == 200, r.text
    return sorted(row[0] for row in r.json()["rows"])
