"""Strategy › Budget's one read (GET /api/v2/usage/limits, backend/usage.py): every bot and pool row carries `spent`, what it
has spent so far in each window whether capped or not, so the page adds spend up the org chart; a person who leads a group
lists that group's bots, may edit them, and gets `leads`; and the read stays a handful of statements a bot at 200 bots."""

import pytest
import contextlib
import json

from backend.store import H, encode
from backend.tests.helpers.api import get, headers  # noqa: F401  (the api fixture)
from backend.tests.helpers.groups import send
from backend.tests.helpers.usage_limits import finish, fleet, groups, put, start
from backend import usage_limits
from backend import bot_settings as BS

pytestmark = pytest.mark.usefixtures("inside_one_half_hour")


def test_every_bot_and_pool_row_says_what_it_spent_in_each_window_capped_or_not(api):
    r = fleet(api)
    groups(api, "product")  # ops in Product, under Engineering
    finish(api, r, start(api, r), 500_000)  # $5, and no limit anywhere
    put(api, "usage/pools/groups/eng", {"daily_usd": 50})
    limits = get(api, "usage/limits")
    ops = limits["bots"]["ops"]
    assert ops["spent"] == {"30m": 5, "hour": 5, "day": 5, "week": 5, "month": 5}
    assert ops["day_spent"] is None  # the capped-window fields stay as they were
    eng, product = limits["pools"]["groups"]["eng"], limits["pools"]["groups"]["product"]
    assert eng["spent"]["day"] == 5 and eng["day_spent"] == 5 and eng["daily_usd"] == 50
    assert product["spent"]["month"] == 5 and product["daily_usd"] is None  # a group with no pool still adds up
    assert limits["pools"]["company"]["spent"]["week"] == 5
    assert limits["leads"] == []


def test_a_person_who_leads_lists_and_may_edit_the_bots_of_what_it_leads(api):
    from backend.tests.helpers.groups import add_bots

    with api.app.state.store.transaction() as c:
        add_bots(c, **{"growth-manager": ({}, "ana"), "sdr": ({}, "growth-manager"), "outside": ({}, "ana")})
    sales = send(
        api,
        "POST",
        "groups",
        {"name": "Sales", "add": {"people": ["cara"], "bots": ["growth-manager"]}, "lead": "person:cara"},
    )["id"]
    outbound = send(api, "POST", "groups", {"name": "Outbound", "parent": sales, "add": {"bots": ["sdr"]}})["id"]
    limits = get(api, "usage/limits", token="cara-test")
    assert limits["leads"] == sorted([sales, outbound]) and limits["may_edit_default"] is False
    assert {"growth-manager", "sdr"} <= set(limits["bots"]) and "outside" not in limits["bots"]
    assert limits["bots"]["sdr"]["may_edit"] is True
    put(api, "usage/limits/sdr", {"daily_usd": 3}, "cara-test")  # what the page offers, the hub takes
    assert get(api, "usage/limits", token="cara-test")["bots"]["sdr"]["daily_usd"] == 3
    # and the spend page shows the same bots, so a led bot's spend is not a false zero
    assert api.get("/api/v2/usage", params={"bot": "sdr"}, headers=headers("cara-test")).status_code == 200
    assert api.get("/api/v2/usage", params={"bot": "outside"}, headers=headers("cara-test")).status_code in (403, 404)


def big_org(api, first, last, groups_=8):
    """`groups_` groups, each with a nested group, every one pooled, and bots `first`..`last - 1` spread over them, each
    with a limit of its own and a $0.25 run today."""
    store = api.app.state.store
    org = {}
    for g in range(groups_):
        org[f"g{g}"] = {"name": f"Group {g}"}
        org[f"g{g}-n"] = {"name": f"Group {g} nested", "parent": f"g{g}"}
    teams = sorted(org)
    with store.transaction() as c:
        people = json.loads(c.execute("SELECT value FROM registry_metadata WHERE key='people'").fetchone()[0])
        people["org_groups"] = org
        c.execute("UPDATE registry_metadata SET value=? WHERE key='people'", (json.dumps(people),))
        for n in range(first, last):
            slug = f"acme-bot-{n:03d}"
            c.execute(
                "INSERT INTO bots(id,slug,display_name,effort,state,created_at) VALUES(?,?,?,?,'active',?)",
                (slug, slug, slug, "", H.now()),
            )
            c.execute("INSERT INTO bot_config(bot,owner_id,team) VALUES(?,?,?)", (slug, "ana", teams[n % len(teams)]))
            BS.write(c, slug, {"name": slug, "runtime": "fake", "model": "m", "host": "keeper"})
            c.execute(
                "INSERT INTO attempts(id,bot_id,created_at,settled_at,est_cost_usd,billing) VALUES(?,?,?,?,?,?)",
                (H.new_id(), slug, H.now(), H.now(), 0.25, "api"),
            )
            usage_limits.save_own(c, slug, {"daily_usd": 10}, "human:ana")
        for gid in org:
            usage_limits.save_pool(c, "group:" + gid, {"daily_usd": 100}, "human:ana")


def statements(api, monkeypatch):
    """GET /api/v2/usage/limits as the owner, and the statements it took."""
    store, seen = api.app.state.store, []
    with monkeypatch.context() as patched:
        for name in ("read", "read_transaction"):

            def traced(opened=getattr(store, name)):
                @contextlib.contextmanager
                def borrowed():
                    with opened() as c:
                        c.set_trace_callback(seen.append)
                        try:
                            yield c
                        finally:
                            c.set_trace_callback(None)

                return borrowed()

            patched.setattr(store, name, traced)
        response = api.get("/api/v2/usage/limits", headers=headers("ana-test"))
    assert response.status_code == 200, response.text
    return response.json(), len(seen)
