"""Groups: one model for the old teams, org groups and derived departments (backend/groups.py, docs/org-chart.md).

The migration keeps every grouping there was, the API lets owners and admins change groups while members read them,
the older `team` and `department` fields follow group membership, and access lists naming a group reach the groups
nested in it.
"""
import json

from backend import groups as Groups
from backend import people as P
from backend.store import H, encode
from backend.tests.test_api import api, get, headers, post  # noqa: F401  (the api fixture)

EVERYONE = {"everyone": True, "people": [], "teams": [], "bots": []}


def send(api, method, path, body=None, token="ana-test", expected=200):
    r = api.request(method, "/api/v2/" + path, json=body, headers=headers(token))
    assert r.status_code == expected, r.text
    return r.json()


def group(api, gid, token="ana-test"):
    return next(row for row in get(api, "groups", token=token) if row["id"] == gid)


def roster_of(api):
    with api.app.state.store.read() as c:
        return json.loads(c.execute("SELECT value_json FROM registry_metadata WHERE key='people'").fetchone()[0])


def set_roster(api, doc):
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE registry_metadata SET value_json=? WHERE key='people'", (encode(doc),))


def add_bots(c, **bots):
    """{slug: (config, reports_to)}: bots straight into the database."""
    for slug, (config, reports_to) in bots.items():
        if not H.bot(c, slug):
            c.execute("INSERT INTO bots(slug,display_name,runtime,model,effort,cwd,host,state,created) "
                      "VALUES(?,?,?,?,?,'','keeper','active',?)", (slug, slug, "fake", "m", "", H.now()))
        c.execute("DELETE FROM bot_config WHERE bot=?", (slug,))
        c.execute("INSERT INTO bot_config(bot,config_json,operator,reports_to) VALUES(?,?,?,?)",
                  (slug, encode({"name": slug, **config}), "ana", reports_to))


def teams_now(api):
    """{bot: group id} as the server reads it now."""
    with api.app.state.store.read() as c:
        roster, configs = Groups._roster(c), Groups._configs(c)
        return {slug: P.team_of(slug, configs, roster) for slug in configs}


# ----------------------------------------------------------------------------- the API
def test_owners_and_admins_manage_groups_and_members_read(api):
    assert get(api, "groups", token="cara-test") == []
    body = {"name": "Marketing", "add": {"people": ["ana"], "bots": ["ops"]}}
    send(api, "POST", "groups", body, token="cara-test", expected=403)
    send(api, "PATCH", "groups/marketing", {"name": "Growth"}, token="cara-test", expected=403)
    send(api, "DELETE", "groups/marketing", token="cara-test", expected=403)
    made = send(api, "POST", "groups", body, token="ben-test")                       # Ben is an admin
    assert {k: made[k] for k in ("id", "name", "parent", "people", "bots")} == {
        "id": "marketing", "name": "Marketing", "parent": "", "people": ["ana"], "bots": ["ops"]}
    assert get(api, "groups", token="cara-test")[0]["bots"] == ["ops"]                # a member reads
    send(api, "POST", "groups", {"name": "marketing"}, expected=409)                 # a name is used once per level
    send(api, "POST", "groups", {"name": "Lost", "parent": "nowhere"}, expected=404)


def test_deleting_a_group_moves_its_teammates_and_groups_up(api):
    send(api, "POST", "groups", {"name": "Marketing"})
    send(api, "POST", "groups", {"name": "SEO", "parent": "marketing", "add": {"people": ["cara"], "bots": ["cpo"]}})
    send(api, "POST", "groups", {"name": "Links", "parent": "seo", "add": {"bots": ["ops"]}})
    send(api, "DELETE", "groups/seo")
    assert group(api, "links")["parent"] == "marketing"
    assert group(api, "marketing")["people"] == ["cara"] and group(api, "marketing")["bots"] == ["cpo"]
    send(api, "DELETE", "groups/marketing")                                           # the last one up: no group at all
    assert group(api, "links")["parent"] == "" and get(api, "groups")[0]["bots"] == ["ops"]
    assert teams_now(api)["cpo"] is None and roster_of(api)["people"][2]["team"] == ""


# ----------------------------------------------------------------------------- what still reads a bot's team and department
def test_naming_a_group_in_an_access_list_reaches_the_groups_nested_in_it(api):
    send(api, "POST", "groups", {"name": "Marketing"})
    send(api, "POST", "groups", {"name": "SEO", "parent": "marketing", "add": {"people": ["cara"]}})
    current = get(api, "bots/finance/access")
    send(api, "PUT", "bots/finance/access", {"see": EVERYONE, "write": EVERYONE, "revision": current["revision"],
                                              "read": {"everyone": False, "people": [], "teams": ["marketing"], "bots": []}})
    assert get(api, "bots/finance", token="cara-test")["access"]["read"] is True          # in SEO, which is in Marketing
    send(api, "PATCH", "groups/seo", {"parent": ""})
    assert get(api, "bots/finance", token="cara-test")["access"]["read"] is False
    assert {"id": "seo", "name": "SEO"} in get(api, "bots/finance/access")["teams"]


def test_a_person_primary_for_a_group_is_primary_for_the_bots_in_the_groups_under_it():
    roster = P.load({"people": [{"id": "ana", "primary_for": ["*"]}, {"id": "ben", "primary_for": ["marketing"]},
                                {"id": "cal", "primary_for": ["seo"]}, {"id": "dee"}],
                     "org_groups": {"marketing": {"name": "Marketing"}, "seo": {"name": "SEO", "parent": "marketing"}}})
    employees = {"cmo": {"team": "marketing"}, "writer": {"team": "seo"}, "loose": {"team": ""}}
    users = lambda slug: {p["id"] for p in P.primary_users(slug, roster, employees)}       # noqa: E731
    assert users("cmo") == {"ana", "ben"} and users("writer") == {"ana", "ben", "cal"} and users("loose") == {"ana"}
    assert P.group_chain("seo", roster) == ["seo", "marketing"] and P.group_and_below("marketing", roster) == {"marketing", "seo"}
    assert P.bot_departments(employees, roster) == {"cmo": "Marketing", "writer": "SEO"}
    # A group that leads back to itself, or under one that is not there, is no child.
    odd = P.load({"org_groups": {"a": {"parent": "b"}, "b": {"parent": "a"}, "c": {"parent": "gone"}}})
    assert odd["org_groups"]["c"]["parent"] == "" and not (odd["org_groups"]["a"]["parent"] and odd["org_groups"]["b"]["parent"])


# ----------------------------------------------------------------------------- the migration
def test_teams_org_groups_and_person_teams_become_groups_and_no_bot_changes_group(api):
    doc = {"default_user": "ana", "teams": {"marketing": {"root": "cmo"}, "product": {"root": "cpo"}},
           "org_groups": {"product": {"name": "Product team", "reports_to": "ana"}, "ops": {"name": "Operations"}},
           "people": [{"id": "ana", "email": "ana@acme.example", "team": "marketing"},
                      {"id": "ben", "email": "ben@acme.example", "team": "Success Team", "reports_to": "ana"},
                      {"id": "cara", "email": "cara@acme.example", "team": "product"}]}
    bots = {"cmo": ({}, None), "seo": ({}, "cmo"), "cpo": ({"team": "product"}, "human:ana"), "design": ({}, "cpo"),
            "coach": ({}, None), "planner": ({"team": ""}, "cmo")}
    with api.app.state.store.transaction() as c:
        c.execute("DELETE FROM bot_config")
        c.execute("UPDATE registry_metadata SET value_json=? WHERE key='people'", (encode(doc),))
        add_bots(c, **bots)
        roster, configs = Groups._roster(c), Groups._configs(c)
        before = {slug: P.team_of(slug, configs, roster) for slug in configs}
    assert before == {"cmo": "marketing", "seo": "marketing", "cpo": "product", "design": "product", "coach": None,
                      "planner": None}
    with api.app.state.store.transaction() as c:
        assert Groups.migrate(c, api.app.state.store.settings) is True
    assert teams_now(api) == before                                        # nobody changed group
    stored = roster_of(api)
    assert "teams" not in stored and stored["groups_migrated"] is True
    assert set(stored["org_groups"]) == {"product", "ops", "marketing", "success-team"}
    assert stored["org_groups"]["product"] == {"name": "Product team", "reports_to": "ana"}     # `reports_to` is not touched
    assert stored["org_groups"]["success-team"] == {"name": "Success Team"}
    assert {p["id"]: p["team"] for p in stored["people"]} == {"ana": "marketing", "ben": "success-team", "cara": "product"}
    with api.app.state.store.read() as c:
        assert json.loads(c.execute("SELECT config_json FROM bot_config WHERE bot='coach'").fetchone()[0])["team"] == ""
        assert c.execute("SELECT team FROM bot_config WHERE bot='design'").fetchone()[0] == "product"
        assert P.by_team(Groups._roster(c))["success-team"] == ["ben"]
        assert json.loads(H.human(c, "ben")["teams_json"]) == ["success-team"]
    # Idempotent: again changes nothing, even when the flag is cleared.
    with api.app.state.store.transaction() as c:
        assert Groups.migrate(c, api.app.state.store.settings) is False
    once = roster_of(api)
    set_roster(api, {k: v for k, v in once.items() if k != "groups_migrated"})
    with api.app.state.store.transaction() as c:
        assert Groups.migrate(c, api.app.state.store.settings) is True
    assert roster_of(api) == once and teams_now(api) == before

