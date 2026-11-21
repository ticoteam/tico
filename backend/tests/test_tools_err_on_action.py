"""Tools changes err on getting done (backend/tools_delegation.py): a bot registers its own bot.yaml tool entry, the
Tools Owner and the Assistant make a Tools change with the rights of the person who asked (recorded as that person, via
the built-in that acted), a scope takes any short name such as `page`, and a bot's Tools tab lists the tools it can use
through grants, not only what its bot.yaml declares."""

import json

from backend.store import H
from backend.tests.helpers.api import assign, claim, get, headers, post, put, ready, runner
from backend.tests.helpers.api_tools import key, level, tool
from backend.tests.helpers.assistant import assistant_turn
from backend.tests.helpers.bot_tools import configure, report
from backend.tests.helpers.credentials import explicit_only, setup as vault
from backend.tests.helpers.member_bots import botops, turn as botops_turn  # noqa: F401  (fixture)

SECRET = "acme-crm-synthetic-0123456789"
PAGE_ENTRY = {
    "service": "meta-ads",
    "identity": "Acme ad account",
    "can": ["read", "write"],
    "scope": {"page": "Acme Page", "ad_account": "act_1001"},
    "env": "META_ACCESS_TOKEN",
}


def active_botops(api):
    with api.app.state.store.transaction() as c:
        H.sync_registry(c, {"botops": {"name": "botops", "status": "active"}}, None)


def turn_of(api, bot, requester="human:ana", title="Connect the Acme CRM for cpo"):
    """A run of `bot` on a task `requester` gave it."""
    machine = runner(api)
    assign(api, machine, bot)
    ready(api, machine, [bot])
    with api.app.state.store.transaction() as c:
        H.task_create(c, requester, title, "Get it working.", "bot:" + bot)
    return claim(api, machine)["token"]


def register(api, bot, token, entry=None, expected=200):
    r = api.post(f"/api/v2/bots/{bot}/tools", json=entry or PAGE_ENTRY, headers=headers(token))
    assert r.status_code == expected, r.text
    return r.json()


def events(api, action):
    with api.app.state.store.read() as c:
        return [
            {"actor": r["actor"], **json.loads(r["detail"] or "{}")}
            for r in c.execute("SELECT actor, detail FROM events WHERE action=? ORDER BY created_at, id", (action,))
        ]


def test_a_bot_registers_its_own_tool_entry_with_a_page_scope_and_another_bot_is_told_who_can(api):
    active_botops(api)
    own = turn_of(api, "ops", requester="bot:cpo")
    made = register(api, "ops", own)
    assert made["tool"]["scope"] == {"page": "Acme Page", "ad_account": "act_1001"}
    assert "page: Acme Page" in made["yaml"]
    refused = register(api, "cpo", own, expected=403)  # another bot's entries are not its own
    assert "Tools Owner" in refused["error"]["detail"]
    # A scope named like a credential is refused, whoever asks.
    register(api, "ops", own, {**PAGE_ENTRY, "service": "meta", "scope": {"page_token": "x"}}, expected=422)


def test_botops_and_the_tools_owner_register_any_bots_entry(api, botops):
    machine = botops
    put(api, "page-owners/tools", {"bot": "ops"})
    owner_run = turn_of(api, "ops", requester="bot:cpo")  # no person behind it: its own rights
    assert register(api, "cpo", owner_run)["task_id"]
    with api.app.state.store.transaction() as c:
        H.task_create(c, "bot:cpo", "Add Meta to cpo", "Its ad account.", "bot:botops")
    botops_run = claim(api, machine, "botops")["token"]
    # Carrying cpo's request, BotOps registers cpo's own entry; another bot's stays closed to cpo's rights.
    assert register(api, "cpo", botops_run, {**PAGE_ENTRY, "identity": "Cpo ads"})["task_id"]
    register(api, "inbox", botops_run, {**PAGE_ENTRY, "identity": "Inbox ads"}, expected=403)


def test_the_tools_owner_sets_a_level_with_the_rights_of_the_person_who_asked_and_says_so(api):
    vault(api)
    tid = tool(api)["id"]
    key(api, tid, SECRET)
    explicit_only(api)  # what explicit grants alone give
    put(api, "page-owners/tools", {"bot": "ops"})
    run = turn_of(api, "ops")  # Ana gave the Tools Owner this task
    assert level(api, tid, "bot:cpo", "read", run)["level"] == "read"
    done = [e for e in events(api, "tool.access_set") if e["subject"] == "bot:cpo"]
    assert done[-1]["actor"] == "human:ana" and done[-1]["via"] == "tools-owner"
    assert done[-1]["delegation"]["for"] == "human:ana" and done[-1]["delegation"]["by"] == "bot:ops"
    # Never to itself: it connects keys for others.
    refused = level(api, tid, "bot:ops", "read", run, expected=403)
    assert "holds none" in refused["error"]["detail"]
    # Taking it back is the same change.
    assert level(api, tid, "bot:cpo", "none", run)["level"] is None


def test_the_tools_owner_defines_a_tool_from_the_catalog_only_never_an_address_of_its_own(api):
    vault(api)
    put(api, "page-owners/tools", {"bot": "ops"})
    run = turn_of(api, "ops")  # Ana gave the Tools Owner this task
    made = post(api, "api-tools", {"name": "Stripe", "service": "stripe"}, run)
    assert made["base_url"] == "https://api.stripe.com" and made["delivery"] == "proxy"
    for body in (
        {"name": "Acme CRM", "base_url": "https://crm.acme.example"},
        {"name": "Stripe", "service": "stripe", "base_url": "https://stripe.acme.example"},
        {"name": "Stripe", "service": "stripe", "check_path": "/v1/customers"},
    ):
        refused = post(api, "api-tools", body, run, expected=403)
        assert refused["error"]["code"] == "catalog_only", body
    r = api.patch(f"/api/v2/api-tools/{made['id']}", json={"base_url": "https://evil.example"}, headers=headers(run))
    assert r.status_code == 403 and r.json()["error"]["code"] == "catalog_only", r.text
    assert api.patch(f"/api/v2/api-tools/{made['id']}", json={"name": "Stripe prod"}, headers=headers(run)).is_success
    # A person's own address: the agent's change of any part (its check path alone) is judged on the whole result.
    own = post(api, "api-tools", {"name": "Stripe EU", "service": "stripe", "base_url": "https://eu.stripe.example"})[
        "id"
    ]
    r = api.patch(f"/api/v2/api-tools/{own}", json={"check_path": "/v1/balance"}, headers=headers(run))
    assert r.status_code == 403 and r.json()["error"]["code"] == "catalog_only", r.text
    # An MCP server not in the catalog is refused before anything reaches its address.
    r = api.post(
        "/api/v2/mcp-tools",
        json={"name": "Acme", "url": "https://mcp.acme.example/mcp", "auth": "oauth"},
        headers=headers(run),
    )
    assert r.status_code == 403 and r.json()["error"]["code"] == "catalog_only", r.text
    # Ana herself adds any address.
    assert post(api, "api-tools", {"name": "Acme CRM", "base_url": "https://crm.acme.example"})["id"]


def test_no_agent_shares_a_website_login_only_a_persons_own_tap_does(api):
    vault(api)
    site = post(api, "website-tools", {"sites": ["app.acme.example"]})["id"]
    account = post(api, f"website-tools/{site}/accounts", {"password": "acme-synthetic-pass-01"})["id"]
    put(api, "page-owners/tools", {"bot": "ops"})
    run = turn_of(api, "ops")  # Ana gave the Tools Owner this task: her rights
    access = f"website-tools/{site}/accounts/{account}/access/"
    for subject in ("bots", "bot:ops", "bot:cpo", "human:cara"):
        assert put(api, access + subject, {"on": True}, run, expected=403)["error"]["code"] == "forbidden", subject
    # A login made by an agent is never shared with every agent either.
    made = api.post(
        f"/api/v2/website-tools/{site}/accounts",
        json={"username": "ops@acme.example", "all_bots": True},
        headers=headers(run),
    )
    assert made.status_code == 403, made.text
    assert put(api, access + "bot:cpo", {"on": True})["on"] is True  # Ana's own tap
    assert put(api, access + "bots", {"on": True})["on"] is True


def test_the_tools_owner_registers_an_agents_mcp_server_only_from_the_catalog_before_botops_hears(api):
    vault(api)
    active_botops(api)
    put(api, "page-owners/tools", {"bot": "ops"})
    run = turn_of(api, "ops")
    entry = {"service": "acme", "can": ["read"], "env": "ACME_TOKEN", "mcp": {"url": "https://mcp.acme.example/mcp"}}
    assert register(api, "cpo", run, entry, expected=403)["error"]["code"] == "catalog_only"
    with api.app.state.store.read() as c:
        assert c.execute("SELECT count(*) FROM bot_tool_requests").fetchone()[0] == 0
    linear = {
        "service": "linear",
        "can": ["read"],
        "env": "LINEAR_API_KEY",
        "mcp": {"url": "https://mcp.linear.app/mcp"},
    }
    assert register(api, "cpo", run, linear)["task_id"]


def test_the_tools_owner_in_its_own_run_adds_or_moves_an_mcp_server_only_from_the_catalog(api):
    active_botops(api)
    configure(api, "cpo")
    put(api, "page-owners/tools", {"bot": "ops"})
    own = turn_of(api, "ops", requester="bot:ops")  # its own task: no one's rights but its own
    acme = {"service": "acme", "can": ["read"], "env": "ACME_TOKEN", "mcp": {"url": "https://mcp.acme.example/mcp"}}
    assert register(api, "cpo", own, acme, expected=403)["error"]["code"] == "catalog_only"
    machine = runner(api)
    assign(api, machine, "cpo")
    linear = {
        "service": "linear",
        "can": ["read"],
        "env": "LINEAR_API_KEY",
        "mcp": {"url": "https://mcp.linear.app/mcp"},
    }
    assert report(api, machine, "cpo", [linear, acme]).status_code == 200
    ids = {t["service"]: t["id"] for t in get(api, "bots/cpo/tools")["tools"]}
    moved = api.post(
        f"/api/v2/bots/cpo/tools/{ids['linear']}/update",
        json={"mcp": {"url": acme["mcp"]["url"]}},
        headers=headers(own),
    )
    assert moved.status_code == 403 and moved.json()["error"]["code"] == "catalog_only", moved.text
    # A person's own server off the catalog: the agent changes nothing the runner sends, its headers included.
    headers_only = {"mcp": {"headers": {"Authorization": "Bearer ${ACME_TOKEN}"}}}
    moved = api.post(f"/api/v2/bots/cpo/tools/{ids['acme']}/update", json=headers_only, headers=headers(own))
    assert moved.status_code == 403 and moved.json()["error"]["code"] == "catalog_only", moved.text
    with api.app.state.store.read() as c:
        assert c.execute("SELECT count(*) FROM bot_tool_requests").fetchone()[0] == 0


def test_botops_with_a_persons_rights_never_shares_a_login_with_every_agent_nor_gives_itself_a_tool(api, botops):
    vault(api)
    site = post(api, "website-tools", {"sites": ["app.acme.example"]})["id"]
    account = post(api, f"website-tools/{site}/accounts", {"password": "acme-synthetic-pass-02"})["id"]
    tid = tool(api)["id"]
    key(api, tid, SECRET)
    put(api, "page-owners/tools", {"bot": "botops"})  # BotOps owns Tools here
    run = botops_turn(api, botops, person="ana-test", text="Tidy our logins and tools")["token"]  # with Ana's rights
    lent = "?on_behalf_of=turn"
    access = f"website-tools/{site}/accounts/{account}/access/"
    assert put(api, access + "bots" + lent, {"on": True}, run, expected=403)["error"]["code"] == "forbidden"
    refused = put(api, f"api-tools/{tid}/access/bot:botops{lent}", {"level": "read"}, run, expected=403)
    assert "holds none" in refused["error"]["detail"]
    acme = {"name": "Acme CRM", "base_url": "https://crm.acme.example"}
    assert post(api, "api-tools" + lent, acme, run, expected=403)["error"]["code"] == "catalog_only"
    # A key never rides on an agent's add; the catalog's tool without one does.
    keyed = post(api, "api-tools" + lent, {"name": "Stripe", "service": "stripe", "key": SECRET}, run, expected=422)
    assert keyed["error"]["code"] == "secret_in_request" and SECRET not in json.dumps(keyed)
    assert post(api, "api-tools" + lent, {"name": "Stripe", "service": "stripe"}, run)["service"] == "stripe"
    # An address an agent brings is the catalog's, before anything reaches it: the probe as well as the add.
    probe = post(api, "mcp-tools/probe" + lent, {"url": "https://mcp.acme.example/mcp"}, run, expected=403)
    assert probe["error"]["code"] == "catalog_only"
    assert put(api, f"api-tools/{tid}/access/bot:cpo{lent}", {"level": "read"}, run)["level"] == "read"  # Ana's rights


def test_a_tools_owner_run_a_bot_started_keeps_that_bots_rights(api):
    vault(api)
    tid = tool(api)["id"]
    key(api, tid, SECRET)
    put(api, "page-owners/tools", {"bot": "ops"})
    run = turn_of(api, "ops", requester="bot:cpo")
    level(api, tid, "bot:inbox", "read", run, expected=403)
    # An ordinary bot is never lent anyone's rights on a Tools change.
    other = turn_of(api, "inbox", title="Give cpo the CRM")
    level(api, tid, "bot:cpo", "read", other, expected=403)


def test_the_assistant_gives_a_bot_a_key_as_its_person_and_a_persons_access_stays_their_tap(api):
    vault(api)
    tid = tool(api)["id"]
    cid = key(api, tid, SECRET)["id"]
    token = assistant_turn(api, "ana-test", "Give cpo read access to the Close CRM")[1]["token"]
    assert level(api, tid, "bot:cpo", "read", token)["level"] == "read"
    done = [e for e in events(api, "tool.access_set") if e["subject"] == "bot:cpo"][-1]
    assert done["actor"] == "human:ana" and done["via"] == "assistant"
    refused = api.post(f"/api/v2/credentials/{cid}/grants", json={"subject": "human:ben"}, headers=headers(token))
    assert refused.status_code == 403 and refused.json()["error"]["code"] == "confirm_required", refused.text
    # A value never goes through the chat.
    blocked = api.post(f"/api/v2/api-tools/{tid}/keys", json={"name": "Again", "value": SECRET}, headers=headers(token))
    assert blocked.status_code == 403


def test_the_assistant_opens_a_key_card_for_a_bot_in_its_persons_chat(api):
    vault(api)
    token = assistant_turn(api, "ana-test", "Get cpo a Close CRM key")[1]["token"]
    opened = post(
        api,
        "credential-requests",
        {"env": "ACME_CRM_TOKEN", "label": "the Acme CRM token", "for_bot": "cpo"},
        token=token,
    )
    with api.app.state.store.read() as c:
        row = c.execute("SELECT * FROM credential_requests WHERE id=?", (opened["id"],)).fetchone()
    assert (row["requester"], row["bot"], row["asker"]) == ("human:ana", "cpo", "assistant")
    assert get(api, "assistant")["room_id"] == row["conversation_id"]
    # It names the bot that needs it: the Assistant holds no key itself.
    mine = api.post("/api/v2/credential-requests", json={"env": "ACME_CRM_TOKEN", "label": "x"}, headers=headers(token))
    assert mine.status_code == 403


def test_the_assistant_hands_the_tools_owner_the_persons_request_and_it_acts_as_them(api):
    with api.app.state.store.transaction() as c:
        H.sync_registry(c, {H.TOOLS_OWNER: {"name": H.TOOLS_OWNER, "status": "active"}}, None)
    vault(api)
    tid = tool(api)["id"]
    key(api, tid, SECRET)
    token = assistant_turn(api, "ana-test", "Give cpo read on the Close CRM")[1]["token"]
    made = api.post(
        "/api/v2/tasks",
        json={"owner": "tools-owner", "title": "Give cpo Read on Close CRM", "body": "cpo, read", "request_id": "turn"},
        headers=headers(token),
    )
    assert made.status_code == 200, made.text  # a Tools Owner task carries the person's request
    with api.app.state.store.read() as c:
        asked = c.execute("SELECT detail FROM events WHERE action='assistant.asked_helper'").fetchone()
    assert json.loads(asked["detail"])["helper"] == "bot:tools-owner"


def test_a_tool_a_member_sees_by_name_alone_never_hides_an_agents_declared_tool_from_her(api):
    vault(api)
    configure(api)
    key(api, tool(api, service="close-crm")["id"], SECRET)  # Everyone; Cara holds nothing on it
    machine = runner(api)
    assign(api, machine, "ops")
    entry = {"service": "crm-sync", "can": ["read"], "env": "CLOSE_API_KEY"}  # the tool's key's variable
    assert report(api, machine, "ops", [entry]).status_code == 200
    rows = {r["id"]: r for r in get(api, "tool-index", "cara-test")["rows"]}
    assert rows["api:close-crm"]["requestable"] is True
    assert "declared:crm-sync" in rows


def test_a_bots_tools_tab_lists_what_it_can_use_through_grants_one_row_per_service(api):
    vault(api)
    configure(api)
    tid = tool(api, service="close-crm")["id"]
    key(api, tid, SECRET)
    level(api, tid, "bot:ops", "read")
    rows = {t["id"]: t for t in get(api, "bots/ops/tools")["tools"]}
    granted = rows["granted-" + tid]
    assert granted["granted"]["level"] == "read" and granted["granted"]["kind"] == "api"
    assert granted["granted"]["href"] == "#/tools/api/" + tid and granted["can"] == ["read"]
    assert SECRET not in json.dumps(rows)
    # Declared in bot.yaml too: one row, marked granted.
    machine = runner(api)
    assign(api, machine, "ops")
    entry = {"service": "close-crm", "can": ["read"], "env": "CLOSE_CRM_KEY", "credential": "not-declared"}
    assert report(api, machine, "ops", [entry]).status_code == 200
    tools = get(api, "bots/ops/tools")["tools"]
    crm = [t for t in tools if t["service"] == "close-crm"]
    assert len(crm) == 1 and crm[0]["granted"]["level"] == "read" and crm[0]["status"] == "ready"
    # A member who neither manages ops nor may see the tool is not told about it.
    seen = api.get("/api/v2/bots/ops/tools", headers=headers("cara-test"))
    assert seen.status_code != 200 or not any(t.get("granted") for t in seen.json()["tools"])


def test_a_confirmed_assistant_act_is_the_persons_own_and_an_unconfirmed_one_is_refused(api):
    """The person's read-back and yes (`confirmed`) makes the Assistant's act theirs: it may share a login and bring an
    address outside the catalog; unconfirmed, neither."""
    from dataclasses import replace

    import pytest

    from backend import tool_access as TA
    from backend.auth import Identity
    from backend.store import Problem

    vault(api)
    site = post(api, "website-tools", {"sites": ["app.acme.example"]})["id"]
    account = post(api, f"website-tools/{site}/accounts", {"password": "acme-synthetic-pass-03"})["id"]
    asked = replace(Identity("human:ana", "owner"), via="assistant", task_actor="bot:assistant")
    yes = replace(asked, confirmed=True)
    v = api.app.state.vault
    acme = "https://mcp.acme.example/mcp"
    with api.app.state.store.read() as c:
        grant = lambda who: TA.decide(c, who, TA.key(account), "bot:cpo", TA.GRANT, admins=v.admins, auth=v.auth).ok  # noqa: E731
        assert grant(yes) is True and grant(asked) is False
    TA.catalog_only(yes, mcp_url=acme)
    with pytest.raises(Problem) as refused:
        TA.catalog_only(asked, mcp_url=acme)
    assert refused.value.code == "catalog_only"
