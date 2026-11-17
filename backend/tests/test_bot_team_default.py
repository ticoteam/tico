"""A bot made under a person takes that person's group by default, and the chart shows it under them (backend/people.py
`manager_team`, settings_admin `_team`). A team the bot names wins."""

import json

from backend import bot_settings as BS
from backend import groups as Groups
from backend import people as P
from backend.store import H, encode
from backend.tests.helpers.api import get, post  # noqa: F401  (the api fixture)


def set_groups(api, groups, teams):
    """Make `groups` ({id: name}) the company's groups and put each person in `teams` ({person id: group id})."""
    with api.app.state.store.transaction() as c:
        doc = json.loads(c.execute("SELECT value FROM registry_metadata WHERE key='people'").fetchone()[0])
        doc["org_groups"] = {gid: {"name": name} for gid, name in groups.items()}
        doc["groups_migrated"] = True
        for person in doc["people"]:
            if person["id"] in teams:
                person["team"] = teams[person["id"]]
        c.execute("UPDATE registry_metadata SET value=? WHERE key='people'", (encode(doc),))


def team_row(api, slug):
    """(the bot's stored team column, its team as the chart reads it, its config's team) for a bot by slug."""
    with api.app.state.store.read() as c:
        bot = H.resolve_bot(c, slug)["id"]
        row = c.execute("SELECT team FROM bot_config WHERE bot=?", (bot,)).fetchone()
        roster, configs = Groups._roster(c), Groups._configs(c)
        return row["team"], P.team_of(bot, configs, roster), BS.read(c, bot).get("team")


def chart_row(api, slug):
    view = get(api, "org")
    return next(row for row in view["bots"] if row["name"] == slug)


def revision(api, slug):
    with api.app.state.store.read() as c:
        bot = H.resolve_bot(c, slug)["id"]
        return c.execute("SELECT revision FROM bot_config WHERE bot=?", (bot,)).fetchone()[0]


# ----------------------------------------------------------------------------- the rule, without a database
def test_a_bot_under_a_person_in_a_group_is_in_that_group_and_team_of_alone_would_not_be():
    roster = P.load(
        {
            "people": [{"id": "arthur", "team": "engineering"}, {"id": "nico", "team": ""}],
            "org_groups": {"engineering": {"name": "Engineering"}},
        }
    )
    employees = {"helper": {"reports_to": "human:arthur"}, "lone": {"reports_to": "human:nico"}}
    assert P.team_of("helper", employees, roster) is None  # team_of stops at the person
    assert P.manager_team("human:arthur", employees, roster) == "engineering"
    assert P.manager_team("helper", employees, roster) == "engineering"  # a bot's manager, walked up through bots
    assert P.manager_team("human:nico", employees, roster) is None
    assert P.manager_team("", employees, roster) is None


def test_a_team_a_bot_names_above_it_is_the_one_its_manager_gets_and_an_empty_one_stops_the_walk():
    roster = P.load(
        {
            "people": [{"id": "arthur", "team": "engineering"}, {"id": "luiz", "team": "sales"}],
            "org_groups": {"engineering": {"name": "Engineering"}, "sales": {"name": "Sales"}},
        }
    )
    employees = {
        "qa-bot": {"team": "sales", "reports_to": "human:arthur"},
        "kept-out": {"team": "", "reports_to": "human:arthur"},
        "under-kept-out": {"reports_to": "kept-out"},
    }
    assert P.manager_team("qa-bot", employees, roster) == "sales"
    assert P.manager_team("kept-out", employees, roster) is None
    assert P.manager_team("under-kept-out", employees, roster) is None
    assert P.manager_team("human:luiz", {}, roster) == "sales"


def test_a_team_that_is_not_a_group_is_no_team():
    roster = P.load(
        {"people": [{"id": "arthur", "team": "retired-group"}], "org_groups": {"engineering": {"name": "E"}}}
    )
    assert P.manager_team("human:arthur", {}, roster) is None


# ----------------------------------------------------------------------------- creation through the API
def test_a_person_in_a_team_makes_a_bot_that_is_in_that_team_under_them(api):
    set_groups(api, {"sales": "Sales"}, {"cara": "sales"})
    post(
        api,
        "bots",
        {"slug": "funnel-daily", "display_name": "Funnel Daily", "description": "Counts leads.", "status": "planned"},
        token="cara-test",
    )  # no manager named: it is hers
    assert team_row(api, "funnel-daily") == ("sales", "sales", "sales")  # the column, the chart's team, the config
    row = chart_row(api, "funnel-daily")
    assert row["team"] == "sales"
    assert row["org_parent"] == "p:cara"


def test_a_person_with_no_team_makes_a_bot_in_no_team(api):
    set_groups(api, {"sales": "Sales"}, {"cara": "sales"})
    post(
        api,
        "bots",
        {
            "slug": "walk-in",
            "display_name": "Walk In",
            "description": "Greets.",
            "status": "planned",
            "reports_to": "human:ben",
        },
        token="ben-test",
    )
    assert team_row(api, "walk-in") == (None, None, None)
    assert chart_row(api, "walk-in")["org_parent"] == "p:ben"


def test_a_team_a_bot_names_still_wins_over_its_manager_when_the_bot_is_edited(api):
    set_groups(api, {"sales": "Sales", "engineering": "Engineering"}, {"cara": "sales"})
    post(
        api,
        "bots",
        {"slug": "scout", "display_name": "Scout", "description": "Scouts.", "status": "planned"},
        token="cara-test",
    )
    assert team_row(api, "scout") == ("sales", "sales", "sales")
    with api.app.state.store.transaction() as c:
        Groups._set_bot(c, H.resolve_bot(c, "scout")["id"], "engineering")
    post(
        api,
        "bots/scout/definition",
        {"description": "Scouts sales news.", "expected_revision": revision(api, "scout")},
        token="cara-test",
    )
    assert team_row(api, "scout") == ("engineering", "engineering", "engineering")
    assert chart_row(api, "scout")["team"] == "engineering"
