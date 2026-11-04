"""The inbox templates (backend/automation_templates): each one is a flow the hub checks once its roles are filled; the fill
resolves roles on the org chart, takes out what the owner may not use, sets the llm step's `as` by owner kind and gives
a group Sort; a person cannot fill one for an inbox they cannot read; and a fill saves nothing."""
from urllib.parse import urlencode

from backend import automation_flow as F
from backend import automation_templates as T
from backend.tests.test_api import api, get, headers, post  # noqa: F401
from backend.tests.test_automation_cli import cli

# The catalog role each bot is made from (the fixture's bots; `ben` operates the product bots, `ana` the rest).
STAFF = {"coo": "general-counsel", "cpo": "engineering-manager", "finance": "finance-lead",
         "product-design": "marketing-lead", "ops": "support-lead", "doc-updater": "sales-lead"}


def set_templates(api, roles):
    """Give bots their catalog template (bot_config's config), a None taking it away."""
    with api.app.state.store.transaction() as c:
        for bot, template in roles.items():
            c.execute("UPDATE bot_config SET config=json_set(config,'$.template',?) WHERE bot=?", (template, bot))


def listed(api, name):
    """A channel on the team's Slack list (Settings > Tools > Slack)."""
    with api.app.state.store.transaction() as c:
        c.execute("INSERT INTO slack_channels(key,name,readers,created_at,updated_at) VALUES(?,?,?,?,?)",
                  ("#" + name, name, "[]", "2026-10-28T00:00:00Z", "2026-10-28T00:00:00Z"))


def fill(api, tid, owner, inbox, token="ana-test", expected=200):
    query = urlencode({"owner": owner, "inbox": inbox})
    return get(api, f"automations/templates/{tid}?{query}", token=token, expected=expected)


def by_id(items):
    return {item["id"]: item for item in items}


def test_every_template_is_a_flow_the_hub_checks_once_filled(api):
    set_templates(api, STAFF)
    for tid in T.ORDER:
        flow = fill(api, tid, "person:ana", "ana@acme.example")
        assert flow["enabled"] is False and flow["owner"] == "person:ana"
        assert flow["trigger"] == {"type": "message", "inbox": "ana@acme.example"}
        F.check(flow)                       # raises F.Invalid when the shape or a reference is wrong


def test_the_roles_resolve_on_the_org_chart(api):
    set_templates(api, STAFF)
    flow = fill(api, "founder", "person:ana", "ana@acme.example")
    dests = by_id(flow["destinations"])
    assert [a["args"]["owner"] for a in dests["legal"]["then"]] == ["bot:coo"]
    assert [a["args"]["owner"] for a in dests["engineering"]["then"]] == ["bot:cpo"]
    assert [a["args"]["owner"] for a in dests["finance"]["then"]] == ["bot:finance"]
    assert by_id(flow["steps"])["draft"]["as"] == "assistant:ana"


def test_a_wake_the_owner_may_not_make_becomes_a_task_for_that_bot(api):
    set_templates(api, STAFF)
    # Ana may not wake product-design (Ben's bot): the marketing destination opens a task for it instead.
    marketing = by_id(fill(api, "founder", "person:ana", "ana@acme.example")["destinations"])["marketing"]
    assert [a["do"] for a in marketing["then"]] == ["create_task"]
    assert marketing["then"][0]["args"]["owner"] == "bot:product-design"


def test_a_wake_the_owner_may_make_stays_a_wake_and_the_bot_turn_runs_as_that_bot(api):
    set_templates(api, STAFF)
    # product-design is its own owner here: it may tell itself, and its llm step runs as itself. The fill is called
    # directly: who may make an automation for a bot is the route's rule, tested with the rest of the access checks.
    with api.app.state.store.read() as c:
        flow = T.fill(c, "founder", "bot:product-design", "ben@acme.example", api.app.state.store.settings)
    F.check(flow)
    marketing = by_id(flow["destinations"])["marketing"]
    assert [a["do"] for a in marketing["then"]] == ["wake_bot"]
    assert marketing["then"][0]["args"]["bot"] == "bot:product-design"
    assert by_id(flow["steps"])["draft"]["as"] == "bot:product-design"


def test_a_destination_with_no_bot_to_reach_is_dropped_with_what_points_at_it(api):
    set_templates(api, {**STAFF, "ops": None})            # no support lead on the chart
    flow = fill(api, "support", "person:ana", "ana@acme.example")
    assert [d["id"] for d in flow["destinations"]] == ["me", "engineering", "finance", "sales", "archive"]
    steps = by_id(flow["steps"])
    assert "customer" not in steps["ask"]["questions"]     # only the rule to Support asked it
    assert all(rule.get("to") != "support" for rule in steps["route"]["rules"])
    assert "support" not in steps["draft"]["choose"]["to"] and steps["draft"]["choose"]["default"] == "me"


def test_a_group_owner_gets_sort_not_a_choose(api):
    set_templates(api, STAFF)
    gid = post(api, "groups", {"name": "Acme Support"})["id"]
    listed(api, "general")                                 # a group reads a Slack channel, not a mailbox
    flow = fill(api, "founder", f"group:{gid}", "slack:#general")
    assert list(F.choosing(flow["steps"])) == []
    assert all("as" not in step for step in flow["steps"])
    sort = by_id(flow["steps"])["draft"]
    assert sort["type"] == "decide" and sort["level"] == "high"
    assert set(sort["questions"]["route"]["criteria"]) == {"me", "legal", "engineering", "finance", "marketing", "archive"}
    # Each option routes to itself, the default too, then the default catches a missing answer: the board's Sort.
    routes = by_id(flow["steps"])["draft_route"]["rules"]
    assert [rule["to"] for rule in routes] == [*sort["questions"]["route"]["criteria"], "me"]
    assert [rule["when"][0]["value"] for rule in routes[:-1]] == list(sort["questions"]["route"]["criteria"])
    assert routes[-1]["when"] == []


def test_blank_is_a_sort_to_me_or_archive(api):
    flow = fill(api, "blank", "person:ana", "ana@acme.example")
    assert [d["id"] for d in flow["destinations"]] == ["me", "archive"]
    assert [step["type"] for step in flow["steps"]] == ["filter", "decide", "filter"]
    assert flow["steps"][0]["rules"] == []
    assert set(flow["steps"][1]["questions"]["route"]["criteria"]) == {"me", "archive"}


def test_a_person_cannot_fill_a_template_for_an_inbox_they_cannot_read(api):
    fill(api, "founder", "person:ana", "ben@acme.example", expected=403)        # Ana's automation on Ben's mail
    fill(api, "founder", "person:ben", "ana@acme.example", token="ben-test", expected=403)
    fill(api, "founder", "person:ana", "ana@acme.example", token="ben-test", expected=403)   # not Ana's to change


def test_a_fill_saves_nothing(api):
    before = get(api, "automations")["automations"]
    fill(api, "founder", "person:ana", "ana@acme.example")
    assert get(api, "automations")["automations"] == before


def test_the_listing_and_an_unknown_template(api):
    listing = get(api, "automations/templates")["templates"]
    assert [t["id"] for t in listing] == ["founder", "support", "sales", "blank"]
    assert all(t["destinations"] for t in listing)
    fill(api, "nope", "person:ana", "ana@acme.example", expected=404)


def test_the_answers_match_the_published_contract(api):
    from backend.tests.test_openapi_v2 import conforms
    set_templates(api, STAFF)
    document = api.get("/api/v2/openapi.json", headers=headers()).json()
    shape = {op["operationId"]: op["responses"]["200"]["content"]["application/json"]["schema"]
             for path in document["paths"].values() for op in path.values()
             if op.get("operationId") in ("listAutomationTemplates", "getAutomationTemplate")}
    assert conforms(get(api, "automations/templates"), shape["listAutomationTemplates"], document) is None
    filled = fill(api, "founder", "person:ana", "ana@acme.example")
    assert conforms(filled, shape["getAutomationTemplate"], document) is None


def test_the_cli_fills_the_same_template_as_the_api(api):
    set_templates(api, STAFF)
    assert [t["id"] for t in cli(api, "ana-test", "templates")["templates"]] == ["founder", "support", "sales", "blank"]
    via_cli = cli(api, "ana-test", "template", "founder", "--owner", "person:ana", "--inbox", "ana@acme.example")
    assert via_cli == fill(api, "founder", "person:ana", "ana@acme.example")
