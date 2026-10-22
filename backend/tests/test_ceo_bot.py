"""The CEO Bot on every install (backend/ceo_bot.py; spec: Head of Product, CEO Bot v3): the built-in bot that was the
Goal Manager is named the CEO Bot everywhere and heads the org chart under the owner, with the Heads and the bots that
report to no one under it. There is no switch a person sees. Only the chart moves: every stored `reports_to` stays as it
was, and so does who may do what. Only the owner renames it."""

import uuid

from backend import ceo_bot
from backend.auth import Identity
from backend.store import H
from backend.tests.test_goal_manager import BASE, with_goal_manager
from backend.tests.test_onboarding import (SUPPORT_AGENT, SUPPORT_CARD, as_person, draft,  # noqa: F401
                                           environment, machine, signed_in)

LEAD_CARD = {"template": "sales-lead", "slug": "sales-lead", "name": "Head of Sales", "required": False,
             "bootstrap": False, "lead": True, "summary": "Runs sales.", "owns": ["the pipeline"], "never": ["sends"],
             "reasoning_effort": "high", "recommend_when": ["sells_to_businesses"]}
HELPER_CARD = {"template": "inbox", "slug": "inbox", "name": "Inbox Manager", "required": False, "kind": "helper",
               "bootstrap": False, "summary": "Runs one person's mailbox.", "owns": ["a mailbox"], "never": ["sends"],
               "reasoning_effort": "high", "recommend_when": ["always"]}
CARDS = BASE + [(LEAD_CARD, ""), (SUPPORT_CARD, SUPPORT_AGENT), (HELPER_CARD, "")]
TEAM = {
    "sales-lead": ("sales-lead", "human:morgan"),     # a Head under the owner
    "eng-lead": ("sales-lead", ""),                  # a Head with no manager
    "support": ("support", "sales-lead"),            # under a Head: stays there
    "scribe": ("support", ""),                       # no manager at all
    "personal": ("support", "human:morgan"),         # the owner's own bot, not a Head: stays under the owner
    "rileys": ("support", "human:riley"),            # another person's bot
    "inbox": ("inbox", ""),                          # a message bot: off the chart
}


def company(environment):
    api = environment(seed={"coo": {"name": "coo", "status": "planned"}, "botops": {"name": "botops", "status": "planned"}},
                      cards=CARDS)
    with_goal_manager(api)
    machine(api)
    draft(api, selected={slug: {"template": template, "display_name": slug.title(), "instructions": "Work.",
                                "reports_to": boss} for slug, (template, boss) in TEAM.items()})
    done = api.post("/api/v2/onboarding/complete", json={}, headers=signed_in())
    assert done.status_code == 200, done.text
    # The team builder put the Heads and their reports in the Heads' department (Sales); the owner has a group too.
    assert {g["id"]: g["bots"] for g in api.get("/api/v2/groups", headers=signed_in()).json()} == \
        {"sales": ["eng-lead", "sales-lead", "support"]}
    home = api.post("/api/v2/groups", json={"name": "Home", "add": {"people": ["morgan"], "bots": ["scribe"]}},
                    headers=signed_in())
    assert home.status_code == 200, home.text
    return api


def chart(api, headers=None):
    view = api.get("/api/v2/org", headers=headers or signed_in()).json()
    return {b["id"]: (b["org_parent"], b["reports_to"], b["display_name"]) for b in view["bots"]}


def groups(api, path="/api/v2/org"):
    return {g["id"]: g["org_parent"] for g in api.get(path, headers=signed_in()).json()["org_groups"]}


def employees(api):
    return {e["name"]: (e["org_parent"], e["display_name"]) for e in api.get("/api/employees", headers=signed_in()).json()}


def stored(api):
    with api.app.state.store.read() as c:
        reports = {r["bot"]: r["reports_to"] for r in c.execute("SELECT bot,reports_to FROM bot_config")}
        names = {r["slug"]: r["display_name"] for r in c.execute("SELECT slug,display_name FROM bots")}
    return reports, names


def as_bot(api, slug):
    """The bot itself, as an agent credential (the way backend/tests/test_health.py signs one in)."""
    token = slug + "-test"
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE bots SET state='active' WHERE slug=?", (slug,))
        c.execute("INSERT OR IGNORE INTO agents(bot,harness,token_hash,created,created_by) "
                  "VALUES(?,'hermes',?,?,'human:morgan')", (slug, "x-" + slug, H.now()))
    api.app.state.store.settings.test_identities[token] = Identity("bot:" + slug, "bot", agent="hermes")
    return {"Authorization": "Bearer " + token, "Idempotency-Key": str(uuid.uuid4())}


def definition(api, body, headers=None):
    with api.app.state.store.read() as c:
        revision = c.execute("SELECT revision FROM bot_config WHERE bot='goal-manager'").fetchone()[0]
    return api.post("/api/v2/bots/goal-manager/definition", json={**body, "expected_revision": revision},
                    headers=headers or signed_in())


def test_every_install_has_the_ceo_bot_at_the_top_of_the_chart(environment):
    """Spec tests 5, 6 and 16: the name in the bot list and the chart; the Heads (with their departments) and the bots
    with no manager under it, it under the owner, the Assistant and message bots not under it. Each stored `reports_to`
    is untouched. No switch is shown or can be flipped."""
    api = company(environment)
    reports_before = stored(api)[0]
    shown = api.get("/api/v2/ceo-bot", headers=signed_in()).json()
    assert "on" not in shown and shown["name"] == "CEO Bot" and shown["level"] == 1 and shown["regular_checks"] is True
    assert "ceo_bot" not in api.get("/api/v2/config", headers=signed_in()).json()
    flip = api.post("/api/v2/ceo-bot", json={"on": False}, headers=signed_in())
    assert flip.status_code == 405, flip.text                                    # nobody turns it off, the owner included
    bots = chart(api)
    assert bots["goal-manager"][:1] == ("p:morgan",) and bots["goal-manager"][2] == "CEO Bot"
    under_ceo = {slug for slug, (parent, _, _) in bots.items() if parent == "b:goal-manager"}
    assert under_ceo == {"sales-lead", "eng-lead", "scribe"}
    assert bots["support"][0] == "b:sales-lead"
    # The Head's department hangs under the CEO Bot with it; the owner's own group stays, or the chart would loop.
    assert groups(api) == groups(api, "/api/humans") == {"sales": "b:goal-manager", "home": ""}
    assert bots["personal"][0] == "p:morgan" and bots["rileys"][0] == "p:riley"
    assert bots["coo"][0] == "p:morgan" and bots["inbox"][0] != "b:goal-manager"       # the Assistant is not under it
    # The chart moved; who each bot reports to did not, in the API or in the database.
    assert {slug: row[1] for slug, row in bots.items()} == {slug: reports_before[slug] or "" for slug in bots}
    reports_after, names = stored(api)
    assert reports_after == reports_before and names["goal-manager"] == "CEO Bot"
    people = employees(api)
    assert people["goal-manager"] == ("p:morgan", "CEO Bot") and people["eng-lead"][0] == "b:goal-manager"


def test_the_ceo_bot_gains_no_access(environment):
    """Heading the chart gives it no new powers: it reads and writes the bots under it on the chart no more than its
    access says."""
    api = company(environment)
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE bot_config SET access_json=? WHERE bot='eng-lead'",
                  ('{"see":{"everyone":true},"read":{"people":["morgan"]},"write":{"people":["morgan"]}}',))
    after = api.get("/api/v2/org", headers=as_bot(api, "goal-manager")).json()
    access = {b["id"]: b["access"] for b in after["bots"]}
    assert access["eng-lead"]["write"] is False and access["eng-lead"]["read"] is False


def test_an_install_from_before_becomes_the_ceo_bot_once_at_upgrade(environment):
    """The migration names an install's Goal Manager the CEO Bot when the server starts, once; a name a person gave it
    stays. Its internal flag is recorded, and running it again changes nothing."""
    api = company(environment)
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE bots SET display_name='Goal Manager' WHERE slug='goal-manager'")     # as an older release left it
        c.execute("DELETE FROM registry_metadata WHERE key=?", (ceo_bot.KEY,))
    from backend.app import create_app
    from fastapi.testclient import TestClient
    with TestClient(create_app(api.app.state.store.settings)):                   # the upgrade: a restart
        pass
    assert stored(api)[1]["goal-manager"] == "CEO Bot"
    with api.app.state.store.transaction() as c:
        assert ceo_bot._record(c)["on"] is True
        assert ceo_bot.migrate(c) is False                                       # once
        c.execute("UPDATE bots SET display_name='Chief' WHERE slug='goal-manager'")
        c.execute("DELETE FROM registry_metadata WHERE key=?", (ceo_bot.KEY,))
        assert ceo_bot.migrate(c) is True
    assert stored(api)[1]["goal-manager"] == "Chief"                             # a person's name is theirs


def test_the_name_sticks_and_its_history_stays_attached(environment):
    """Spec test 16's name half: same slug and actor, so its readings and messages read as the CEO Bot. A setup run
    again or an update does not name it back."""
    api = company(environment)
    kpi = api.post("/api/v2/kpis", json={"name": "Weekly signups", "owner": "company", "cadence": "weekly"},
                   headers=signed_in())
    assert kpi.status_code == 200, kpi.text
    kid = kpi.json()["kpi"]["id"]
    logged = api.post(f"/api/v2/kpis/{kid}/readings", json={"value": 12}, headers=as_bot(api, "goal-manager"))
    assert logged.status_code == 200, logged.text
    readings = api.get(f"/api/v2/kpis/{kid}/readings", headers=signed_in()).json()["readings"]
    assert [r["actor"] for r in readings] == ["bot:goal-manager"]
    from backend import names
    with api.app.state.store.read() as c:
        assert names.lookup(c, {"bot:goal-manager"}) == {"bot:goal-manager": "CEO Bot"}
    assert api.post("/api/v2/onboarding/complete", json={}, headers=signed_in()).status_code == 200
    from backend.app import create_app
    from fastapi.testclient import TestClient
    with TestClient(create_app(api.app.state.store.settings)):                   # a restart is an update
        pass
    assert stored(api)[1]["goal-manager"] == "CEO Bot"
    refused = api.post("/api/v2/goal-manager/turn-on", json={}, headers=as_person(api, "quinn"))
    assert refused.status_code == 403 and "CEO Bot" in refused.json()["error"]["detail"]


def test_only_the_owner_renames_it_and_the_new_name_shows_everywhere(environment):
    """Spec test 24 on the server: the owner renames it with the bot's Name field; an admin, a member and a bot are
    refused (a built-in bot is the owner's alone, backend/auth.py `bot_manager`). The new name is what the chart, the
    bot list, its settings and its messages say, and it survives a setup run again and an update."""
    api = company(environment)
    for headers in (as_person(api, "riley"), as_person(api, "quinn"), as_bot(api, "botops"), as_bot(api, "goal-manager")):
        refused = definition(api, {"display_name": "Atlas"}, headers)
        assert refused.status_code == 403, refused.text
    assert stored(api)[1]["goal-manager"] == "CEO Bot"
    renamed = definition(api, {"display_name": "Atlas"})
    assert renamed.status_code == 200, renamed.text
    assert chart(api)["goal-manager"][2] == "Atlas" and employees(api)["goal-manager"][1] == "Atlas"
    assert api.get("/api/v2/ceo-bot", headers=signed_in()).json()["name"] == "Atlas"
    from backend import names
    with api.app.state.store.read() as c:
        assert names.lookup(c, {"bot:goal-manager"}) == {"bot:goal-manager": "Atlas"}
        assert ceo_bot.name(c) == "Atlas"
    assert api.post("/api/v2/onboarding/complete", json={}, headers=signed_in()).status_code == 200
    from backend.app import create_app
    from fastapi.testclient import TestClient
    with TestClient(create_app(api.app.state.store.settings)):
        pass
    assert stored(api)[1]["goal-manager"] == "Atlas"


# Where the old name may still be written: the upgrade that renames it, and the UI's fallback for a roster read before it.
OLD_NAME_ALLOWED = {"backend/ceo_bot.py", "backend/store.py", "ui/app/notices.js", "ui/app/state.js"}


def test_the_old_name_is_nowhere_a_person_sees():
    """v3: "Goal Manager" is gone from the app, its clients, its help and its templates. The docs are the Technical
    Writer's sweep (docs/, README, SECURITY, PRIVACY) and the tests may name it as an install from before."""
    import pathlib
    import subprocess
    root = pathlib.Path(__file__).resolve().parents[2]
    found = subprocess.run(["git", "grep", "-l", ceo_bot.OLD_NAME, "--", "backend", "clients", "templates", "ui",
                            "runner-rs", ":!backend/tests", ":!ui/tests"], cwd=root, capture_output=True, text=True)
    assert set(found.stdout.split()) <= OLD_NAME_ALLOWED
