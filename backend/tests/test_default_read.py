"""Default read (backend/default_read.py): every bot reads a company tool with no grant while its "Everyone can read"
switch is on, exactly as a grant at Read reaches a run; off, only explicit grants do. A tool whose Read cannot be held to
reads, an Only listed tool and the Tools page's owner never read by default, and an explicit grant always wins."""

import json

from backend import api_tools, default_read
from backend.tests.helpers.api import assign, claim, get, headers, post, put, ready, runner
from backend.tests.helpers.api_tools import key, level, tool
from backend.tests.helpers.credentials import setup

SECRET = "acme-close-synthetic-rw-0011223344"
READ_SECRET = "acme-close-synthetic-ro-5566778899"
DB_URL = "postgresql://reader:acme-synthetic-db-7788@db.acme.example/app"


def turn(api, bot="ops"):
    machine = runner(api)
    assign(api, machine, bot)
    ready(api, machine, [bot])
    post(api, "chat/" + bot, {"text": "Use only the synthetic fixture"})
    return claim(api, machine)


def delivered(api, attempt):
    return {i["env"]: i for i in get(api, "credential-runtime", attempt["token"])["credentials"]}


def switch(api, tid, on, token="ana-test", expected=200):
    r = api.patch(f"/api/v2/api-tools/{tid}", json={"everyone_reads": on}, headers=headers(token))
    assert r.status_code == expected, r.text
    return r.json()


def held(api, tid, bot="bot:ops"):
    with api.app.state.store.read() as c:
        found = api_tools._held(c, tid, bot)
        return [(k["access_level"], lv) for k, lv in found]


def test_every_bot_reads_a_tool_by_default_until_the_switch_is_off(api):
    setup(api)
    crm = tool(api)["id"]  # Close CRM, on the proxy
    key(api, crm, SECRET)
    post(api, f"api-tools/{crm}/settings", {"name": "CLOSE_REGION", "value": "eu"})
    attempt = turn(api)
    items = delivered(api, attempt)
    assert items["CLOSE_API_KEY"]["value"].startswith("tpx1.") and items["CLOSE_REGION"]["value"] == "eu"
    assert held(api, crm) == [("read_write", "read")]  # the proxy holds it to reads
    row = {t["id"]: t for t in get(api, "bots/ops/tools")["tools"]}["granted-" + crm]
    assert row["granted"]["default"] is True and row["granted"]["level"] == "read" and row["can"] == ["read"]
    page = get(api, f"api-tools/{crm}")
    assert page["everyone_reads"] == {"on": True, "enforceable": True} and page["access"] == []
    switch(api, tid=crm, on=False, token="cara-test", expected=403)  # a credential administrator's switch
    assert switch(api, crm, False)["everyone_reads"] is False
    assert "CLOSE_API_KEY" not in delivered(api, attempt) and held(api, crm) == []
    with api.app.state.store.read() as c:
        event = c.execute("SELECT actor, detail FROM events WHERE action='tool.everyone_reads_changed'").fetchone()
    assert event["actor"] == "human:ana" and json.loads(event["detail"]) == {"before": True, "after": False}
    switch(api, crm, True)
    assert "CLOSE_API_KEY" in delivered(api, attempt)


def test_only_where_read_is_enforceable_and_an_explicit_grant_wins(api):
    setup(api)
    # A database URL on computers, with no address the proxy can call: "read" would hand out the write key.
    db = tool(api, "Acme Warehouse")["id"]
    post(api, f"api-tools/{db}/keys", {"name": "Warehouse", "value": DB_URL, "access_level": "read_write"})
    assert get(api, f"api-tools/{db}")["everyone_reads"] == {"on": True, "enforceable": False}
    # The same with a stored Read key: that key itself is safe to hand out.
    files = tool(api, "Acme Files")["id"]
    post(api, f"api-tools/{files}/keys", {"name": "Files ro", "value": READ_SECRET, "access_level": "read"})
    # An Only listed tool never default-reads.
    hidden = tool(api, "Acme Bank", base_url="https://api.bank.acme.example")["id"]
    key(api, hidden, SECRET + "-bank")
    r = api.patch(
        f"/api/v2/api-tools/{hidden}",
        json={"audience": {"everyone": False, "people": [], "teams": [], "bots": []}},
        headers=headers(),
    )
    assert r.status_code == 200, r.text
    crm = tool(api)["id"]
    key(api, crm, SECRET)
    with api.app.state.store.read() as c:
        counted = default_read.summary(c)
    assert counted == {
        "tools": 4,
        "default_read": 2,
        "switched_off": 0,
        "not_enforceable": 1,
        "only_listed": 1,
        "website": 0,
    }
    attempt = turn(api)
    items = delivered(api, attempt)
    assert DB_URL not in json.dumps(items) and held(api, db) == []
    assert [
        i["value"] for i in items.values() if i["id"] in {k["id"] for k in get(api, f"api-tools/{files}")["keys"]}
    ] == [READ_SECRET]
    assert held(api, hidden) == []
    # An explicit Read and write wins over the default Read.
    level(api, crm, "bot:ops", "read_write")
    assert held(api, crm) == [("read_write", "read_write")]
    with api.app.state.store.read() as c:
        assert api_tools.key_for(c, crm, "bot:ops")[1] == "read_write"
    # The Tools page's owner holds no key, default or not.
    put(api, "page-owners/tools", {"bot": "cpo"})
    assert held(api, crm, "bot:cpo") == [] and held(api, files, "bot:cpo") == []


def test_a_variable_an_explicit_grant_gives_is_not_given_again_by_default(api):
    setup(api)
    crm = tool(api)["id"]
    key(api, crm, SECRET)
    other = tool(api, "Acme CRM mirror", base_url="https://api.crm.acme.example")["id"]
    post(
        api,
        f"api-tools/{other}/keys",
        {"name": "Mirror", "value": SECRET + "-m", "access_level": "read", "env": "CLOSE_API_KEY"},
    )
    level(api, other, "bot:ops", "read")
    assert held(api, crm) == []  # ops already gets CLOSE_API_KEY from its explicit grant
    # Two tools by default under one variable: one value per variable, the first tool's.
    assert held(api, other, "bot:inbox") == [("read", "read")] and held(api, crm, "bot:inbox") == []
