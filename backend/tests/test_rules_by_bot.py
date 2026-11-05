"""GET /api/v2/rules/by-teammate, the Rules view of the org chart (backend/org_rules.py `teammate_rules`): the rules in force,
and for each bot and person the caller may see, the rules whose Who or With names it. Acme is the fictional company, as in
test_org_rules.py: Ana owns it, Ben is an admin, Cara is a plain member of Support, and Finance sits in Support Tier 2."""
import json

import pytest

from backend import org_rules as O
from backend.auth import Identity
from backend.store import H, Problem, encode
from backend.tests.test_api import api, get, headers  # noqa: F401  (the api fixture)
from backend.tests.test_openapi_v2 import conforms
from backend.tests.test_org_rules import org, rule


def add_people(api, *people):
    with api.app.state.store.transaction() as c:
        doc = json.loads(c.execute("SELECT value FROM registry_metadata WHERE key='people'").fetchone()[0])
        doc["people"].extend(people)
        c.execute("UPDATE registry_metadata SET value=? WHERE key='people'", (encode(doc),))
        H.sync_registry(c, {}, doc)


def set_access(api, path, token="ana-test", **levels):
    current = api.get(f"/api/v2/{path}/access", headers=headers(token))
    revision = current.json()["revision"] if current.status_code == 200 else 0
    body = {"see": {"everyone": True}, "read": {"everyone": True}, "write": {"everyone": True},
            "revision": revision, **levels}
    r = api.put(f"/api/v2/{path}/access", json=body, headers=headers(token))
    assert r.status_code == 200, r.text


def teammates(api, token="ana-test"):
    r = api.get("/api/v2/rules/by-teammate", headers=headers(token))
    assert r.status_code == 200, r.text
    return r.json()


def by_slug(view, slug):
    return next((b for b in view["bots"] if b["slug"] == slug), None)


def by_id(view, pid):
    return next((p for p in view["people"] if p["id"] == pid), None)


def test_a_group_rule_reaches_a_person_and_a_bot_in_a_nested_group(api):
    org(api)
    add_people(api, {"id": "dan", "email": "dan@acme.example", "team": "support-tier-2", "name": "Dan"})
    support = rule(api, {"kind": "internal", "text": "Log every refund.", "who": {"groups": ["support"]}})["id"]
    view = teammates(api)
    assert by_id(view, "dan") == {"id": "dan", "who": [support], "with": []}    # Dan is in Support Tier 2, under Support
    assert by_id(view, "cara")["who"] == [support]
    assert by_slug(view, "finance")["who"] == [support]                          # Finance is in Support Tier 2
    assert by_slug(view, "ops") is None and by_id(view, "ben") is None           # no rule names them


def test_who_and_with_are_listed_apart(api):
    org(api)
    r = rule(api, {"kind": "internal", "text": "Cara checks every note to Ops.",
                   "who": {"people": ["cara"]}, "with": {"bots": ["ops"]}})["id"]
    view = teammates(api)
    assert by_id(view, "cara") == {"id": "cara", "who": [r], "with": []}
    assert by_slug(view, "ops") == {"slug": "ops", "who": [], "with": [r]}


def test_all_bots_reaches_bots_and_no_person(api):
    org(api)
    everyone_bots = rule(api, {"kind": "internal", "text": "Bots cite their source.", "who": {"all_bots": True}})["id"]
    view = teammates(api)
    assert by_slug(view, "ops")["who"] == [everyone_bots]
    assert by_slug(view, "finance")["who"] == [everyone_bots]
    assert view["people"] == []


def test_suggested_and_retired_rules_are_left_out(api):
    org(api)
    active = rule(api, {"kind": "internal", "text": "Cara signs off on credits.", "who": {"people": ["cara"]}})["id"]
    suggested = rule(api, {"kind": "internal", "text": "Cara answers in a day.", "who": {"people": ["cara"]}},
                     token="cara-test")
    assert suggested["status"] == "suggested"
    retired = rule(api, {"kind": "internal", "text": "Finance checks every credit.", "who": {"bots": ["finance"]}})["id"]
    assert api.post(f"/api/v2/rules/{retired}/retire", json={"version": 1}, headers=headers()).status_code == 200
    view = teammates(api)
    assert [r["id"] for r in view["rules"]] == [active]
    assert by_id(view, "cara")["who"] == [active]
    assert by_slug(view, "finance") is None


def test_a_bot_the_caller_cannot_see_and_an_archived_bot_are_left_out(api):
    org(api)
    r = rule(api, {"kind": "internal", "text": "Finance logs refunds.", "who": {"bots": ["finance"]}})["id"]
    set_access(api, "bots/finance", see={"people": ["ben"]}, read={"people": ["ben"]}, write={"people": ["ben"]})
    assert by_slug(teammates(api, "cara-test"), "finance") is None
    assert by_slug(teammates(api, "ben-test"), "finance")["who"] == [r]
    assert by_slug(teammates(api), "finance")["who"] == [r]
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE bots SET state='archived' WHERE slug='finance'")
    assert by_slug(teammates(api), "finance") is None


def test_a_hidden_person_and_one_the_caller_cannot_see_are_left_out(api):
    org(api)
    add_people(api, {"id": "dan", "email": "dan@acme.example", "team": "support", "name": "Dan"},
               {"id": "frank", "email": "frank@acme.example", "team": "support", "name": "Frank", "hidden": True})
    support = rule(api, {"kind": "internal", "text": "Log every refund.", "who": {"groups": ["support"]}})["id"]
    view = teammates(api)
    assert by_id(view, "dan")["who"] == [support]
    assert by_id(view, "frank") is None                                  # off the org chart
    set_access(api, "humans/dan", see={"people": ["ben"]}, read={"people": ["ben"]}, write={"people": ["ben"]})
    assert by_id(teammates(api, "cara-test"), "dan") is None             # Cara cannot see Dan
    assert by_id(teammates(api, "ben-test"), "dan")["who"] == [support]


def test_the_people_and_bots_are_the_ones_the_org_chart_shows(api):
    org(api)
    add_people(api, {"id": "dan", "email": "dan@acme.example", "team": "support-tier-2", "name": "Dan"},
               {"id": "frank", "email": "frank@acme.example", "team": "support", "name": "Frank", "hidden": True})
    rule(api, {"kind": "internal", "text": "Log every refund.", "who": {"groups": ["support"]}})
    rule(api, {"kind": "internal", "text": "Bots cite their source.", "who": {"all_bots": True}})
    set_access(api, "humans/dan", see={"people": ["ben"]}, read={"people": ["ben"]}, write={"people": ["ben"]})
    for token in ("ana-test", "ben-test", "cara-test"):
        chart = get(api, "org", token)
        mine = teammates(api, token)
        assert {p["id"] for p in mine["people"]} <= {p["id"] for p in chart["people"]}, token
        assert {b["slug"] for b in mine["bots"]} <= {b["slug"] for b in chart["bots"]}, token


def test_can_edit_is_true_for_an_admin_and_an_owner_and_false_for_a_plain_member(api):
    org(api)
    r = rule(api, {"kind": "internal", "text": "Log every refund.", "who": {"groups": ["support"]}})["id"]
    edits = {token: next(x["can_edit"] for x in teammates(api, token)["rules"] if x["id"] == r)
             for token in ("ana-test", "ben-test", "cara-test")}
    assert edits == {"ana-test": True, "ben-test": True, "cara-test": False}


def test_a_runner_credential_is_refused_as_the_rules_list_refuses_it(api):
    """The read gate both routes share (`auth.domain`): a runner is no teammate, so it reads no rules. The HTTP layer
    rejects a runner token before the route, so the gate is called directly."""
    org(api)
    rule(api, {"kind": "internal", "text": "Log every refund.", "who": {"groups": ["support"]}})
    with api.app.state.store.read() as c:
        with pytest.raises(Problem) as refused:
            O.teammate_rules(c, api.app.state.auth, Identity("runner:ci", "runner"))
    assert refused.value.status == 403


def test_a_person_gets_the_rules_the_list_route_gives_her(api):
    """No person is denied a rule in force (every teammate reads every sentence), so the rules the new route returns are
    the ones GET /api/v2/rules returns to that person, active ones only."""
    org(api)
    rule(api, {"kind": "internal", "text": "Log every refund.", "who": {"groups": ["support"]}})
    rule(api, {"kind": "internal", "text": "Bots cite their source.", "who": {"all_bots": True}})
    for token in ("cara-test", "ben-test"):
        listed = api.get("/api/v2/rules", headers=headers(token)).json()["rules"]
        assert {r["id"] for r in listed} == {r["id"] for r in teammates(api, token)["rules"]}


def test_the_answer_matches_the_declared_schema(api):
    org(api)
    rule(api, {"kind": "internal", "text": "Log every refund.", "who": {"groups": ["support"]}})
    document = api.get("/api/v2/openapi.json", headers=headers()).json()
    schema = document["paths"]["/api/v2/rules/by-teammate"]["get"]["responses"]["200"]["content"]["application/json"]["schema"]
    assert conforms(teammates(api), schema, document) is None
