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
from backend.tests.helpers.credentials import setup as vault
from backend.tests.helpers.member_bots import botops  # noqa: F401  (fixture)

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
