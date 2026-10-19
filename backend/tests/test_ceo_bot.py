"""The CEO Bot rollout switch (backend/ceo_bot.py): off by default, and off nothing changes; on, the Goal Manager is
named the CEO Bot everywhere and heads the org chart under the owner, with the Heads and the bots that report to no one
under it. Only the chart moves: every stored `reports_to` stays as it was, and so does who may do what."""

import uuid

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


def switch(api, on, headers=None):
    return api.post("/api/v2/ceo-bot", json={"on": on}, headers=headers or signed_in())


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


def test_off_by_default_and_off_nothing_changes(environment):
    """Spec test 17's guard: an install that never turns it on keeps the Goal Manager by that name, off the chart's
    top, and a switch turned on and off again gives back exactly what it had."""
    api = company(environment)
    assert api.get("/api/v2/ceo-bot", headers=signed_in()).json()["on"] is False
    assert api.get("/api/v2/config", headers=signed_in()).json()["ceo_bot"] is False
    before_chart, before_people, before_stored = chart(api), employees(api), stored(api)
    assert groups(api) == {"sales": "", "home": ""}
    assert before_chart["goal-manager"][2] == "Goal Manager" and before_stored[1]["goal-manager"] == "Goal Manager"
    assert before_chart["sales-lead"][0] == "p:morgan" and before_chart["scribe"][0] == "p:morgan"
    assert switch(api, True).json()["on"] is True
    assert switch(api, False).json() == {**switch(api, False).json(), "on": False, "name": "Goal Manager"}
    assert chart(api) == before_chart and employees(api) == before_people and stored(api) == before_stored
    assert groups(api) == groups(api, "/api/humans") == {"sales": "", "home": ""}


def test_on_it_is_the_ceo_bot_and_heads_the_chart(environment):
    """Spec tests 5 and 6: the name in the bot list and the chart; the Heads (with their departments) and the bots with
    no manager under it, it under the owner, the Assistant and message bots not under it. Each stored `reports_to` is
    untouched."""
    api = company(environment)
    reports_before, _ = stored(api)
    result = switch(api, True)
    assert result.status_code == 200, result.text
    assert result.json()["on"] is True and result.json()["changed_by"] == "human:morgan"
    assert api.get("/api/v2/config", headers=signed_in()).json()["ceo_bot"] is True
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


def test_on_the_ceo_bot_gains_no_access(environment):
    """No new powers in this slice: the CEO Bot reads and writes the bots under it on the chart no more than before."""
    api = company(environment)
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE bot_config SET access_json=? WHERE bot='eng-lead'",
                  ('{"see":{"everyone":true},"read":{"people":["morgan"]},"write":{"people":["morgan"]}}',))
    before = api.get("/api/v2/org", headers=as_bot(api, "goal-manager")).json()
    switch(api, True)
    after = api.get("/api/v2/org", headers=as_bot(api, "goal-manager")).json()
    access = {b["id"]: b["access"] for b in after["bots"]}
    assert access == {b["id"]: b["access"] for b in before["bots"]}
    assert access["eng-lead"]["write"] is False


def test_only_owners_and_admins_flip_it_and_no_bot(environment):
    api = company(environment)
    member = switch(api, True, as_person(api, "quinn"))
    assert member.status_code == 403
    for bot in ("goal-manager", "botops"):
        refused = switch(api, True, as_bot(api, bot))
        assert refused.status_code == 403, refused.text
    assert api.get("/api/v2/ceo-bot", headers=signed_in()).json()["on"] is False
    assert switch(api, True, as_person(api, "riley")).json()["on"] is True        # an Admin
    assert switch(api, False).json()["on"] is False                               # the owner


def test_the_name_sticks_and_its_history_stays_attached(environment):
    """Spec test 16's name half: same slug and actor, so its readings and messages read as the CEO Bot. A setup run
    again or an update does not name it back, and a name a person gives it while on is theirs after off."""
    api = company(environment)
    kpi = api.post("/api/v2/kpis", json={"name": "Weekly signups", "owner": "company", "cadence": "weekly"},
                   headers=signed_in())
    assert kpi.status_code == 200, kpi.text
    kid = kpi.json()["kpi"]["id"]
    logged = api.post(f"/api/v2/kpis/{kid}/readings", json={"value": 12}, headers=as_bot(api, "goal-manager"))
    assert logged.status_code == 200, logged.text
    switch(api, True)
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
    with api.app.state.store.read() as c:
        revision = c.execute("SELECT revision FROM bot_config WHERE bot='goal-manager'").fetchone()[0]
    renamed = api.post("/api/v2/bots/goal-manager/definition",
                       json={"display_name": "Chief", "expected_revision": revision}, headers=signed_in())
    assert renamed.status_code == 200, renamed.text
    switch(api, False)
    assert stored(api)[1]["goal-manager"] == "Chief"
