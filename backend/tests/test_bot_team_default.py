"""A bot made under a person takes that person's group by default, and the chart shows it under them (backend/people.py
`manager_team`, settings_admin `_team`). A team the bot names wins. Bots made before the default get their manager's
group from migration 0061, once, and only the ones it should (backend/migrations/0061_bot_team_from_manager.py)."""
import importlib
import json
import sqlite3

from backend import groups as Groups
from backend import people as P
from backend.store import H, encode
from backend.tests.test_api import api, get, post  # noqa: F401  (the api fixture)

MIGRATION = importlib.import_module("backend.migrations.0061_bot_team_from_manager")


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
        row = c.execute("SELECT team, config FROM bot_config WHERE bot=?", (bot,)).fetchone()
        roster, configs = Groups._roster(c), Groups._configs(c)
        return row["team"], P.team_of(bot, configs, roster), json.loads(row["config"]).get("team")


def chart_row(api, slug):
    view = get(api, "org")
    return next(row for row in view["bots"] if row["name"] == slug)


def revision(api, slug):
    with api.app.state.store.read() as c:
        bot = H.resolve_bot(c, slug)["id"]
        return c.execute("SELECT revision FROM bot_config WHERE bot=?", (bot,)).fetchone()[0]


# ----------------------------------------------------------------------------- the rule, without a database
def test_a_bot_under_a_person_in_a_group_is_in_that_group_and_team_of_alone_would_not_be():
    roster = P.load({"people": [{"id": "arthur", "team": "engineering"}, {"id": "nico", "team": ""}],
                     "org_groups": {"engineering": {"name": "Engineering"}}})
    employees = {"helper": {"reports_to": "human:arthur"}, "lone": {"reports_to": "human:nico"}}
    assert P.team_of("helper", employees, roster) is None           # team_of stops at the person
    assert P.manager_team("human:arthur", employees, roster) == "engineering"
    assert P.manager_team("helper", employees, roster) == "engineering"   # a bot's manager, walked up through bots
    assert P.manager_team("human:nico", employees, roster) is None
    assert P.manager_team("", employees, roster) is None


def test_a_team_a_bot_names_above_it_is_the_one_its_manager_gets_and_an_empty_one_stops_the_walk():
    roster = P.load({"people": [{"id": "arthur", "team": "engineering"}, {"id": "luiz", "team": "sales"}],
                     "org_groups": {"engineering": {"name": "Engineering"}, "sales": {"name": "Sales"}}})
    employees = {"qa-bot": {"team": "sales", "reports_to": "human:arthur"},
                 "kept-out": {"team": "", "reports_to": "human:arthur"},
                 "under-kept-out": {"reports_to": "kept-out"}}
    assert P.manager_team("qa-bot", employees, roster) == "sales"
    assert P.manager_team("kept-out", employees, roster) is None
    assert P.manager_team("under-kept-out", employees, roster) is None
    assert P.manager_team("human:luiz", {}, roster) == "sales"


def test_a_team_that_is_not_a_group_is_no_team():
    roster = P.load({"people": [{"id": "arthur", "team": "retired-group"}], "org_groups": {"engineering": {"name": "E"}}})
    assert P.manager_team("human:arthur", {}, roster) is None


# ----------------------------------------------------------------------------- creation through the API
def test_a_person_in_a_team_makes_a_bot_that_is_in_that_team_under_them(api):
    set_groups(api, {"sales": "Sales"}, {"cara": "sales"})
    post(api, "bots", {"slug": "funnel-daily", "display_name": "Funnel Daily", "description": "Counts leads.",
                       "status": "planned"}, token="cara-test")          # no manager named: it is hers
    assert team_row(api, "funnel-daily") == ("sales", "sales", "sales")    # the column, the chart's team, the config
    row = chart_row(api, "funnel-daily")
    assert row["team"] == "sales"
    assert row["org_parent"] == "p:cara"


def test_a_person_with_no_team_makes_a_bot_in_no_team(api):
    set_groups(api, {"sales": "Sales"}, {"cara": "sales"})
    post(api, "bots", {"slug": "walk-in", "display_name": "Walk In", "description": "Greets.", "status": "planned",
                       "reports_to": "human:ben"}, token="ben-test")
    assert team_row(api, "walk-in") == (None, None, None)
    assert chart_row(api, "walk-in")["org_parent"] == "p:ben"


def test_a_team_a_bot_names_still_wins_over_its_manager_when_the_bot_is_edited(api):
    set_groups(api, {"sales": "Sales", "engineering": "Engineering"}, {"cara": "sales"})
    post(api, "bots", {"slug": "scout", "display_name": "Scout", "description": "Scouts.", "status": "planned"},
         token="cara-test")
    assert team_row(api, "scout") == ("sales", "sales", "sales")
    with api.app.state.store.transaction() as c:
        Groups._set_bot(c, H.resolve_bot(c, "scout")["id"], "engineering")
    post(api, "bots/scout/definition", {"description": "Scouts sales news.", "expected_revision": revision(api, "scout")},
         token="cara-test")
    assert team_row(api, "scout") == ("engineering", "engineering", "engineering")
    assert chart_row(api, "scout")["team"] == "engineering"


# ----------------------------------------------------------------------------- migration 0061
def _db():
    conn = sqlite3.connect(":memory:")
    conn.executescript("""
        CREATE TABLE bots(id TEXT PRIMARY KEY, slug TEXT);
        CREATE TABLE bot_config(bot TEXT PRIMARY KEY, config TEXT NOT NULL, team TEXT, reports_to TEXT);
        CREATE TABLE registry_metadata(key TEXT PRIMARY KEY, value TEXT);
    """)
    roster = {"default_user": "arthur", "groups_migrated": True,
              "people": [{"id": "arthur", "team": "engineering"}, {"id": "luiz", "team": "sales", "inbox_bot": "inbox-luiz"},
                         {"id": "nico", "team": ""}, {"id": "ghost", "team": "retired"}],
              "org_groups": {"engineering": {"name": "Engineering"}, "sales": {"name": "Sales"},
                             "qa": {"name": "QA", "root": "qa-lead"}}}
    conn.execute("INSERT INTO registry_metadata VALUES('people', ?)", (encode(roster),))
    return conn


def _bot(conn, bot, reports_to="", column=None, config=None, slug=None):
    """A bot as the database holds it: `column` is bot_config.team, `config` is what its config names (a `team` key only
    when given)."""
    conn.execute("INSERT INTO bots VALUES(?,?)", (bot, slug or bot))
    conn.execute("INSERT INTO bot_config VALUES(?,?,?,?)",
                 (bot, json.dumps({"name": slug or bot, **(config or {})}), column, reports_to))


def _seed(conn):
    _bot(conn, "arthur-dev-orchestrator", "human:arthur")
    _bot(conn, "arthur-frontend-reviewer", "arthur-dev-orchestrator")
    _bot(conn, "qa-lead", "human:arthur")
    _bot(conn, "funnel-daily", "qa-lead")
    _bot(conn, "luiz-stats", "human:luiz")
    _bot(conn, "nico-bot", "human:nico")
    _bot(conn, "ghost-bot", "human:ghost")
    _bot(conn, "orphan", "")
    _bot(conn, "explicit-empty", "human:arthur", config={"team": ""})
    _bot(conn, "already-sales", "human:arthur", column="sales", config={"team": "sales"})
    _bot(conn, "assistant", "human:arthur", slug="assistant")
    _bot(conn, "book-helper", "human:arthur", config={"template": "bookkeeper"})
    _bot(conn, "inbox-luiz", "human:luiz")


def _state(conn):
    return {bot: (team, json.loads(config).get("team", "<absent>"))
            for bot, team, config in conn.execute("SELECT bot, team, config FROM bot_config ORDER BY bot")}


def test_migration_gives_a_team_less_bot_its_managers_group_and_leaves_the_others():
    conn = _db()
    _seed(conn)
    MIGRATION.up(conn)
    state = _state(conn)
    assert state["arthur-dev-orchestrator"] == ("engineering", "engineering")
    assert state["arthur-frontend-reviewer"] == ("engineering", "engineering")   # up the chain, through a bot
    assert state["luiz-stats"] == ("sales", "sales")
    assert state["funnel-daily"] == ("qa", "<absent>")       # under a root bot: its column follows the chain, no copy
    assert state["qa-lead"] == ("qa", "<absent>")
    assert state["nico-bot"] == (None, "<absent>")           # a manager with an empty team
    assert state["ghost-bot"] == (None, "<absent>")          # a manager whose team is not a group
    assert state["orphan"] == (None, "<absent>")
    assert state["explicit-empty"] == (None, "")             # kept out of groups on purpose: left as it is
    assert state["already-sales"] == ("sales", "sales")
    assert state["assistant"] == (None, "<absent>")          # built-ins stay out of groups
    assert state["book-helper"] == (None, "<absent>")        # helpers too
    assert state["inbox-luiz"] == (None, "<absent>")         # and the inbox bots


def test_migration_runs_again_and_changes_nothing():
    conn = _db()
    _seed(conn)
    MIGRATION.up(conn)
    once = _state(conn)
    MIGRATION.up(conn)
    assert _state(conn) == once


def test_migration_on_a_new_file_with_no_tables_does_nothing():
    conn = sqlite3.connect(":memory:")
    MIGRATION.up(conn)
    assert conn.execute("SELECT count(*) FROM sqlite_master").fetchone()[0] == 0
