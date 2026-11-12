"""Groups: one model for the old teams, org groups and derived departments (backend/groups.py, docs/org-chart.md).

The migration keeps every grouping there was, the API lets owners and admins change groups while members read them,
the older `team` and `department` fields follow group membership, and access lists naming a group reach the groups
nested in it.
"""

from backend import groups as Groups
from backend import people as P
from backend.store import H, encode
from backend.tests.helpers.api import get, post
from backend import bot_settings as BS
from backend.tests.helpers.groups import add_bots, group, roster_of, send, set_roster

EVERYONE = {"everyone": True, "people": [], "teams": [], "bots": []}


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
    made = send(api, "POST", "groups", body, token="ben-test")  # Ben is an admin
    assert {k: made[k] for k in ("id", "name", "parent", "people", "bots")} == {
        "id": "marketing",
        "name": "Marketing",
        "parent": "",
        "people": ["ana"],
        "bots": ["ops"],
    }
    assert get(api, "groups", token="cara-test")[0]["bots"] == ["ops"]  # a member reads
    send(api, "POST", "groups", {"name": "marketing"}, expected=409)  # a name is used once per level
    send(api, "POST", "groups", {"name": "Lost", "parent": "nowhere"}, expected=404)


def test_deleting_a_group_moves_its_teammates_and_groups_up(api):
    send(api, "POST", "groups", {"name": "Marketing"})
    send(api, "POST", "groups", {"name": "SEO", "parent": "marketing", "add": {"people": ["cara"], "bots": ["cpo"]}})
    send(api, "POST", "groups", {"name": "Links", "parent": "seo", "add": {"bots": ["ops"]}})
    send(api, "DELETE", "groups/seo")
    assert group(api, "links")["parent"] == "marketing"
    assert group(api, "marketing")["people"] == ["cara"] and group(api, "marketing")["bots"] == ["cpo"]
    send(api, "DELETE", "groups/marketing")  # the last one up: no group at all
    assert group(api, "links")["parent"] == "" and get(api, "groups")[0]["bots"] == ["ops"]
    assert teams_now(api)["cpo"] is None and roster_of(api)["people"][2]["team"] == ""


# ----------------------------------------------------------------------------- what still reads a bot's team and department
def test_naming_a_group_in_an_access_list_reaches_the_groups_nested_in_it(api):
    send(api, "POST", "groups", {"name": "Marketing"})
    send(api, "POST", "groups", {"name": "SEO", "parent": "marketing", "add": {"people": ["cara"]}})
    current = get(api, "bots/finance/access")
    send(
        api,
        "PUT",
        "bots/finance/access",
        {
            "see": EVERYONE,
            "write": EVERYONE,
            "revision": current["revision"],
            "read": {"everyone": False, "people": [], "teams": ["marketing"], "bots": []},
        },
    )
    assert get(api, "bots/finance", token="cara-test")["access"]["read"] is True  # in SEO, which is in Marketing
    send(api, "PATCH", "groups/seo", {"parent": ""})
    assert get(api, "bots/finance", token="cara-test")["access"]["read"] is False
    assert {"id": "seo", "name": "SEO"} in get(api, "bots/finance/access")["teams"]


def test_a_person_primary_for_a_group_is_primary_for_the_bots_in_the_groups_under_it():
    roster = P.load(
        {
            "people": [
                {"id": "ana", "primary_for": ["*"]},
                {"id": "ben", "primary_for": ["marketing"]},
                {"id": "cal", "primary_for": ["seo"]},
                {"id": "dee"},
            ],
            "org_groups": {"marketing": {"name": "Marketing"}, "seo": {"name": "SEO", "parent": "marketing"}},
        }
    )
    employees = {"cmo": {"team": "marketing"}, "writer": {"team": "seo"}, "loose": {"team": ""}}
    users = lambda slug: {p["id"] for p in P.primary_users(slug, roster, employees)}  # noqa: E731
    assert users("cmo") == {"ana", "ben"} and users("writer") == {"ana", "ben", "cal"} and users("loose") == {"ana"}
    assert P.group_chain("seo", roster) == ["seo", "marketing"] and P.group_and_below("marketing", roster) == {
        "marketing",
        "seo",
    }
    assert P.bot_departments(employees, roster) == {"cmo": "Marketing", "writer": "SEO"}
    # A group that leads back to itself, or under one that is not there, is no child.
    odd = P.load({"org_groups": {"a": {"parent": "b"}, "b": {"parent": "a"}, "c": {"parent": "gone"}}})
    assert odd["org_groups"]["c"]["parent"] == "" and not (
        odd["org_groups"]["a"]["parent"] and odd["org_groups"]["b"]["parent"]
    )


# ----------------------------------------------------------------------------- the migration
def test_teams_org_groups_and_person_teams_become_groups_and_no_bot_changes_group(api):
    doc = {
        "default_user": "ana",
        "teams": {"marketing": {"root": "cmo"}, "product": {"root": "cpo"}},
        "org_groups": {"product": {"name": "Product team", "reports_to": "ana"}, "ops": {"name": "Operations"}},
        "people": [
            {"id": "ana", "email": "ana@acme.example", "team": "marketing"},
            {"id": "ben", "email": "ben@acme.example", "team": "Success Team", "reports_to": "ana"},
            {"id": "cara", "email": "cara@acme.example", "team": "product"},
        ],
    }
    bots = {
        "cmo": ({}, None),
        "seo": ({}, "cmo"),
        "cpo": ({"team": "product"}, "human:ana"),
        "design": ({}, "cpo"),
        "coach": ({}, None),
        "planner": ({"team": ""}, "cmo"),
    }
    with api.app.state.store.transaction() as c:
        c.execute("DELETE FROM bot_config")
        c.execute("UPDATE registry_metadata SET value=? WHERE key='people'", (encode(doc),))
        add_bots(c, **bots)
        roster, configs = Groups._roster(c), Groups._configs(c)
        before = {slug: P.team_of(slug, configs, roster) for slug in configs}
    assert before == {
        "cmo": "marketing",
        "seo": "marketing",
        "cpo": "product",
        "design": "product",
        "coach": None,
        "planner": None,
    }
    with api.app.state.store.transaction() as c:
        assert Groups.migrate(c, api.app.state.store.settings) is True
    assert teams_now(api) == before  # nobody changed group
    stored = roster_of(api)
    assert "teams" not in stored and stored["groups_migrated"] is True
    assert set(stored["org_groups"]) == {"product", "ops", "marketing", "success-team"}
    assert stored["org_groups"]["product"] == {
        "name": "Product team",
        "reports_to": "ana",
    }  # `reports_to` is not touched
    assert stored["org_groups"]["success-team"] == {"name": "Success Team"}
    assert {p["id"]: p["team"] for p in stored["people"]} == {
        "ana": "marketing",
        "ben": "success-team",
        "cara": "product",
    }
    with api.app.state.store.read() as c:
        assert BS.read(c, 'coach')["team"] == ""
        assert c.execute("SELECT team FROM bot_config WHERE bot='design'").fetchone()[0] == "product"
        assert P.by_team(Groups._roster(c))["success-team"] == ["ben"]
        assert H.human(c, "ben")["teams"] == ["success-team"]
    # Idempotent: again changes nothing, even when the flag is cleared.
    with api.app.state.store.transaction() as c:
        assert Groups.migrate(c, api.app.state.store.settings) is False
    once = roster_of(api)
    set_roster(api, {k: v for k, v in once.items() if k != "groups_migrated"})
    with api.app.state.store.transaction() as c:
        assert Groups.migrate(c, api.app.state.store.settings) is True
    assert roster_of(api) == once and teams_now(api) == before


# ----------------------------------------------------------------------------- the General group
def test_the_built_in_group_is_kept_by_tico_and_setup_puts_built_in_bots_in_it(api):
    with api.app.state.store.transaction() as c:
        add_bots(c, botops=({"template": "botops"}, None))
        Groups.place(c, api.app.state.store.settings, ["botops"])
    assert group(api, "built-in")["name"] == "General"
    assert teams_now(api)["botops"] == "built-in"
    send(api, "PATCH", "groups/built-in", {"name": "Helpers"}, expected=422)
    send(api, "DELETE", "groups/built-in", expected=422)
    send(api, "POST", "groups", {"name": "Marketing", "add": {"bots": ["botops"]}}, expected=422)  # by hand: never
    assert teams_now(api)["botops"] == "built-in"


def test_migration_puts_the_built_in_bots_in_the_built_in_group_and_no_one_else(api):
    doc = {"default_user": "ana", "people": [{"id": "ana", "email": "ana@acme.example"}], "org_groups": {}}
    with api.app.state.store.transaction() as c:
        c.execute("DELETE FROM bot_config")
        c.execute("UPDATE registry_metadata SET value=? WHERE key='people'", (encode(doc),))
        add_bots(c, botops=({"template": "botops"}, None), cmo=({}, None))
        assert Groups.migrate(c, api.app.state.store.settings) is True
    assert teams_now(api) == {"botops": "built-in", "cmo": None}
    assert group(api, "built-in")["name"] == "General"
