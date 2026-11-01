"""Strategy › Budget's one read (GET /api/v2/usage/limits, backend/usage.py): every bot and pool row carries `spent`, what it
has spent so far in each window whether capped or not, so the page adds spend up the org chart; a person who leads a group
lists that group's bots, may edit them, and gets `leads`; and the read stays a handful of statements a bot at 200 bots."""
import contextlib
import json

from backend.store import H, encode
from backend.tests.test_api import api, get, headers  # noqa: F401  (the api fixture)
from backend.tests.test_groups import send
from backend.tests.test_usage_limits import finish, fleet, groups, put, start


def test_every_bot_and_pool_row_says_what_it_spent_in_each_window_capped_or_not(api):
    r = fleet(api)
    groups(api, "product")                                                  # ops in Product, under Engineering
    finish(api, r, start(api, r), 500_000)                                  # $5, and no limit anywhere
    put(api, "usage/pools/groups/eng", {"daily_usd": 50})
    limits = get(api, "usage/limits")
    ops = limits["bots"]["ops"]
    assert ops["spent"] == {"30m": 5, "hour": 5, "day": 5, "week": 5, "month": 5}
    assert ops["day_spent"] is None                                         # the capped-window fields stay as they were
    eng, product = limits["pools"]["groups"]["eng"], limits["pools"]["groups"]["product"]
    assert eng["spent"]["day"] == 5 and eng["day_spent"] == 5 and eng["daily_usd"] == 50
    assert product["spent"]["month"] == 5 and product["daily_usd"] is None  # a group with no pool still adds up
    assert limits["pools"]["company"]["spent"]["week"] == 5
    assert limits["leads"] == []


def test_a_person_who_leads_lists_and_may_edit_the_bots_of_what_it_leads(api):
    from backend.tests.test_groups import add_bots
    with api.app.state.store.transaction() as c:
        add_bots(c, **{"sales-lead": ({}, "ana"), "sdr": ({}, "sales-lead"), "outside": ({}, "ana")})
    sales = send(api, "POST", "groups", {"name": "Sales", "add": {"people": ["cara"], "bots": ["sales-lead"]},
                                         "lead": "person:cara"})["id"]
    outbound = send(api, "POST", "groups", {"name": "Outbound", "parent": sales, "add": {"bots": ["sdr"]}})["id"]
    limits = get(api, "usage/limits", token="cara-test")
    assert limits["leads"] == sorted([sales, outbound]) and limits["may_edit_default"] is False
    assert {"sales-lead", "sdr"} <= set(limits["bots"]) and "outside" not in limits["bots"]
    assert limits["bots"]["sdr"]["may_edit"] is True
    put(api, "usage/limits/sdr", {"daily_usd": 3}, "cara-test")             # what the page offers, the hub takes
    assert get(api, "usage/limits", token="cara-test")["bots"]["sdr"]["daily_usd"] == 3


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
            c.execute("INSERT INTO bots(slug,display_name,runtime,model,effort,cwd,host,state,created_at) "
                      "VALUES(?,?,?,?,?,'','keeper','active',?)", (slug, slug, "fake", "m", "", H.now()))
            c.execute("INSERT INTO bot_config(bot,config,operator,team) VALUES(?,?,?,?)",
                      (slug, encode({"name": slug}), "ana", teams[n % len(teams)]))
            c.execute("INSERT INTO turns(id,bot,started_at,finished_at,est_cost_usd,billing) VALUES(?,?,?,?,?,?)",
                      (H.new_id(), slug, H.now(), H.now(), 0.25, "api"))
            c.execute("INSERT INTO usage_limits(bot,daily_usd,updated_at,updated_by) VALUES(?,?,?,?)",
                      (slug, 10, H.now(), "human:ana"))
        for gid in org:
            c.execute("INSERT OR IGNORE INTO usage_pools(scope,daily_usd,updated_at,updated_by) VALUES(?,?,?,?)",
                      ("group:" + gid, 100, H.now(), "human:ana"))


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


def test_the_read_for_200_bots_in_nested_pooled_groups_is_a_few_statements_a_bot(api, monkeypatch):
    big_org(api, 0, 20)
    _, small = statements(api, monkeypatch)
    big_org(api, 20, 200)
    limits, big = statements(api, monkeypatch)
    ours = {slug: row for slug, row in limits["bots"].items() if slug.startswith("acme-bot-")}
    assert len(ours) == 200
    in_g0 = sum(1 for n in range(200) if n % 16 in (0, 1))                   # g0 and the group nested in it
    assert limits["pools"]["groups"]["g0"]["spent"]["day"] == in_g0 * 0.25
    assert ours["acme-bot-001"]["pools"] == ["group:g0-n", "group:g0"]      # nearest group first
    # A pool and each window are read once a pass, not once a bot: what grows with the bots is the bot's own row.
    assert (big - small) / 180 < 1, (small, big)
