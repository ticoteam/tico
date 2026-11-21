"""Needs you is the one queue (master plan P1): it lists the person's open access requests, key cards and key-drop
proposals, each through its own list and authority check, and only to someone who may settle it. Ana owns the company
and Ben is an admin (both credential administrators); Cara is a member. Ops owns the Tools page."""

from backend import api_tools
from backend.tests.helpers import credentials as creds
from backend.tests.helpers.api import claim, get, headers, post, put
from backend.tests.helpers.api_tools import key, tool
from backend.tests.helpers.chat_goals import setup
from backend.tests.helpers.events import live, ready, topic  # noqa: F401  (live: the stream fixture)

OLD = "acme-close-synthetic-old-0011223344"
NEW = "acme-close-synthetic-new-5566778899"
ADS = "acme-ads-synthetic-rw-0011223344"
PASSWORD = "".join(["Tr0ub", "4dor&3x"])
KINDS = ("access", "key", "key_drop")


def world(api, monkeypatch):
    """An open access request (Read and write on Acme Ads for cpo), a Reconnect key card (Close CRM's key was refused)
    and a key-drop proposal (JIRA_LOGIN for Close CRM, in Ana's chat with ops)."""
    machine, _ = setup(api)
    put(api, "page-owners/tools", {"bot": "ops"})
    dm = post(api, "threads/dm", {"members": ["bot:ops"]})
    creds.setup(api)
    reply = [401]
    monkeypatch.setattr(api_tools, "send_check", lambda url, hdrs, params: api_tools.Reply(reply[0]))
    crm = tool(api)["id"]
    key(api, crm, OLD)
    ads = tool(api, "Acme Ads", base_url="https://ads.acme.example")["id"]
    key(api, ads, ADS)
    asked = post(api, "access-requests", {"resource": ads, "level": "read_write", "for_bot": "cpo", "why": "pause ads"})
    assert asked["state"] == "open"
    assert post(api, f"api-tools/{crm}/check", {})["detail"] == "Key rejected"
    post(
        api,
        "credentials",
        {
            "name": "JIRA_LOGIN",
            "kind": "password",
            "username": "chris",
            "env": "JIRA_LOGIN",
            "secret": PASSWORD,
            "source": "tools-chat",
        },
    )
    post(api, f"threads/{dm['id']}/messages", {"text": "for close [saved JIRA_LOGIN for Close CRM]"})
    bot = claim(api, machine)["token"]
    rows = [{"key": "JIRA_LOGIN", "tool": crm, "level": "read", "bots": ["cpo"]}]
    proposal = post(api, "tools-chat/proposals", {"thread_id": dm["id"], "rows": rows}, bot)
    reply[0] = 200
    return asked["id"], proposal["id"], ads


def requests(api, token):
    return {it["kind"]: it for it in get(api, "needs-you", token)["items"] if it["kind"] in KINDS}


def test_needs_you_lists_each_request_only_to_someone_who_may_settle_it_and_its_card_settles_it(api, monkeypatch):
    rid, mid, ads = world(api, monkeypatch)
    mine = requests(api, "ana-test")
    assert set(mine) == set(KINDS)
    assert mine["access"]["access"]["id"] == rid and mine["access"]["access"]["level"] == "read_write"
    assert mine["key_drop"]["key_drop"]["message_id"] == mid
    assert [r["key"] for r in mine["key_drop"]["key_drop"]["rows"]] == ["JIRA_LOGIN"]
    assert PASSWORD not in str(mine) and OLD not in str(mine)
    assert set(requests(api, "ben-test")) == {"access"}  # the key card and the proposal are Ana's
    assert get(api, "needs-you?count=true", "ana-test")["count"] >= 3

    # Cara can settle none of them: she sees none, and each action refuses her.
    assert requests(api, "cara-test") == {}
    refusals = [
        post(api, f"access-requests/{rid}/grant", {}, "cara-test", expected=403),
        post(api, f"credential-requests/{mine['key']['key']['id']}/save", {"value": NEW}, "cara-test", expected=404),
        post(
            api, f"tools-chat/proposals/{mid}/grant", {"keep": [0]}, "cara-test", expected=404
        ),  # she can't see the tool
    ]
    assert not any(name in str(refusals) for name in ("Ana", "Ben", "human:"))  # a refusal names nobody

    # Ben's Read only settles every copy: Ana's list drops it too, and cpo reads Acme Ads, no more.
    assert post(api, f"access-requests/{rid}/grant", {"level": "read"}, "ben-test")["state"] == "granted"
    with api.app.state.store.read() as c:
        assert api_tools.subject_level(c, ads, "bot:cpo") == "read"
    assert post(api, f"credential-requests/{mine['key']['key']['id']}/save", {"value": NEW})["status"] == "saved"
    assert post(api, f"tools-chat/proposals/{mid}/grant", {"keep": [0]})["state"] == "granted"
    assert requests(api, "ana-test") == {} and requests(api, "ben-test") == {}


def test_a_held_page_never_serves_a_request_the_viewer_lost(api, monkeypatch):
    # The next page of a list built seconds ago is cut from that build: every request in it (the counts name them too)
    # is checked again first, not only the page's own items.
    world(api, monkeypatch)
    task = post(api, "tasks", {"owner": "ana", "title": "Coverage", "body": "Please."})
    post(api, f"tasks/{task['id']}/asks", {"to": "ben", "line": "OK Monday", "kind": "ok"})
    first = get(api, "needs-you?limit=1", "ben-test")
    assert first["next"] and [it["kind"] for it in first["items"]] == ["access"]
    api.app.state.store.settings.credential_admins = ("ana@acme.example",)  # Ben may grant nothing now
    after = get(api, f"needs-you?limit=1&cursor={first['next']}", "ben-test")
    assert [it for it in after["items"] if it["kind"] in KINDS] == []
    assert not set(after["by_kind"]) & set(KINDS)


def test_a_key_proposal_is_listed_and_granted_only_by_someone_who_can_see_its_tool(api, monkeypatch):
    # Ben, a credential administrator, drops a key in his chat with ops; once its tool is Only listed without him, the
    # proposal leaves his list and its Grant finds nothing.
    machine, _ = setup(api)
    put(api, "page-owners/tools", {"bot": "ops"})
    creds.setup(api)
    monkeypatch.setattr(api_tools, "send_check", lambda url, hdrs, params: api_tools.Reply(200))
    crm = tool(api)["id"]
    dm = post(api, "threads/dm", {"members": ["bot:ops"]}, "ben-test")
    secret = {"name": "JIRA_LOGIN", "kind": "password", "env": "JIRA_LOGIN", "secret": PASSWORD, "source": "tools-chat"}
    post(api, "credentials", secret, "ben-test")
    post(api, f"threads/{dm['id']}/messages", {"text": "for close [saved JIRA_LOGIN for Close CRM]"}, "ben-test")
    bot = claim(api, machine)["token"]
    rows = [{"key": "JIRA_LOGIN", "tool": crm, "level": "read", "bots": ["cpo"]}]
    proposal = post(api, "tools-chat/proposals", {"thread_id": dm["id"], "rows": rows}, bot)
    assert set(requests(api, "ben-test")) == {"key_drop"}
    nobody = {"audience": {"everyone": False, "people": [], "teams": [], "bots": []}}
    assert api.patch(f"/api/v2/api-tools/{crm}", json=nobody, headers=headers()).status_code == 200
    assert requests(api, "ben-test") == {}
    post(api, f"tools-chat/proposals/{proposal['id']}/grant", {"keep": [0]}, "ben-test", expected=404)


def test_a_new_key_card_reaches_its_person_on_the_live_stream(api, monkeypatch, live):  # noqa: F811
    setup(api)
    put(api, "page-owners/tools", {"bot": "ops"})
    creds.setup(api)
    monkeypatch.setattr(api_tools, "send_check", lambda url, hdrs, params: api_tools.Reply(401))
    crm = tool(api)["id"]
    key(api, crm, OLD)
    stream = live("ana-test", "?topics=needs")
    ready(stream)
    with api.app.state.store.read() as c:
        before = c.execute("SELECT COALESCE(MAX(seq),0) FROM changes").fetchone()[0]
    assert post(api, f"api-tools/{crm}/check", {})["detail"] == "Key rejected"
    stream.until(lambda e: topic("needs")(e) and any(it["kind"] == "key" for it in e["data"].get("items") or []))
    with api.app.state.store.read() as c:
        rows = c.execute("SELECT kind FROM changes WHERE seq>? AND topic='needs' AND subject_id='human:ana'", (before,))
        assert "key" in {r[0] for r in rows}


def test_a_refused_key_save_names_nobody(api, monkeypatch):
    # Ben connected Close CRM, so its Reconnect card is his; once he is no credential administrator his save is
    # refused, in words naming nobody, and the card leaves his list.
    setup(api)
    put(api, "page-owners/tools", {"bot": "ops"})
    creds.setup(api)
    monkeypatch.setattr(api_tools, "send_check", lambda url, hdrs, params: api_tools.Reply(401))
    crm = post(api, "api-tools", {"name": "Close CRM"}, "ben-test")["id"]
    key(api, crm, OLD, token="ben-test")
    assert post(api, f"api-tools/{crm}/check", {}, "ben-test")["detail"] == "Key rejected"
    card = requests(api, "ben-test")["key"]["key"]
    api.app.state.store.settings.credential_admins = ("ana@acme.example",)
    assert requests(api, "ben-test") == {}
    refused = post(api, f"credential-requests/{card['id']}/save", {"value": NEW}, "ben-test", expected=403)
    assert refused["error"]["detail"] == "You can't save this key"
    assert not any(name in str(refused) for name in ("Ana", "Ben", "human:"))


def test_a_key_card_is_listed_and_saved_only_while_its_requester_manages_the_agent(api):
    # Ops reports to Cara, so its key card is hers to fill; once it reports elsewhere, the card leaves her list and its
    # Save is refused, though she is a credential administrator who may store keys: one check for both.
    machine, _ = setup(api)
    creds.setup(api)
    api.app.state.store.settings.credential_admins = ("ana@acme.example", "cara@acme.example")
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE bot_config SET reports_to='human:cara' WHERE bot='ops'")
    post(api, "chat/ops", {"text": "Connect our analytics"}, "cara-test")
    bot = claim(api, machine)["token"]
    card = post(api, "credential-requests", {"env": "ACME_ANALYTICS_KEY", "label": "the analytics key"}, bot)
    assert set(requests(api, "cara-test")) == {"key"}
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE bot_config SET reports_to='human:ana' WHERE bot='ops'")
    assert requests(api, "cara-test") == {}
    refused = post(api, f"credential-requests/{card['id']}/save", {"value": NEW}, "cara-test", expected=403)
    assert refused["error"]["detail"] == "You can't save this key"


def test_a_key_proposal_whose_tools_are_all_deleted_is_superseded(api, monkeypatch):
    _, mid, _ = world(api, monkeypatch)
    with api.app.state.store.read() as c:
        crm = c.execute("SELECT id FROM tools WHERE name='Close CRM'").fetchone()[0]
    assert api.delete(f"/api/v2/api-tools/{crm}", headers=headers()).status_code == 200
    assert "key_drop" not in requests(api, "ana-test")
    # A keep that does not say it names places on the card as proposed is refused first, even with no row left.
    post(api, f"tools-chat/proposals/{mid}/grant", {"keep": [0]}, expected=409)
    assert (
        post(api, f"tools-chat/proposals/{mid}/grant", {"keep": [0], "positions": "original"})["state"] == "superseded"
    )


def test_grant_takes_a_rows_tool_by_its_id_alone(api, monkeypatch):
    # A tool named like the row's tool id is added: Grant attaches the key to the row's own tool, never that one.
    _, mid, _ = world(api, monkeypatch)
    with api.app.state.store.read() as c:
        crm = c.execute("SELECT id FROM tools WHERE name='Close CRM'").fetchone()[0]
    named = tool(api, crm.upper())["id"]
    assert named != crm
    card = requests(api, "ana-test")["key_drop"]["key_drop"]
    body = {"keep": [0], "seen": card["losses"], "positions": "original"}
    assert post(api, f"tools-chat/proposals/{mid}/grant", body)["state"] == "granted"
    with api.app.state.store.read() as c:
        assert c.execute("SELECT tool_id FROM credentials WHERE name='JIRA_LOGIN'").fetchone()[0] == crm
    # Grant's stored rows name a tool by its id alone: a name finds nothing there.
    import pytest

    from backend import key_grants
    from backend.store import Problem

    with api.app.state.store.read() as c:
        assert key_grants._tool(c, "Close CRM")["id"] == crm  # pyright: ignore[reportPrivateUsage]
        with pytest.raises(Problem):
            key_grants._tool(c, "Close CRM", exact=True)  # pyright: ignore[reportPrivateUsage]


def test_a_grant_from_a_card_whose_loss_preview_moved_is_refused(api, monkeypatch):
    # Two tabs show the same proposal; a read key is put on Close CRM, so the proposal's row now replaces it. The tab
    # still showing the old preview is refused with a 409; one showing the new preview grants.
    _, mid, _ = world(api, monkeypatch)
    stale = requests(api, "ana-test")["key_drop"]["key_drop"]
    with api.app.state.store.read() as c:
        crm = c.execute("SELECT id FROM tools WHERE name='Close CRM'").fetchone()[0]
    key(api, crm, "acme-close-synthetic-read-0099887766", "read")
    fresh = requests(api, "ana-test")["key_drop"]["key_drop"]
    assert fresh["losses"] != stale["losses"] and fresh["rows"][0]["replaces"]["key"] == "Close read"
    body = {"keep": [0], "seen": stale["losses"]}
    assert post(api, f"tools-chat/proposals/{mid}/grant", body, expected=409)["error"]["code"] == "changed"
    assert post(api, f"tools-chat/proposals/{mid}/grant", body | {"seen": fresh["losses"]})["state"] == "granted"


def test_a_grant_refusal_says_not_found_for_a_hidden_tool_before_forbidden(api, monkeypatch):
    from backend import key_grants

    world(api, monkeypatch)
    with api.app.state.store.read() as c:
        crm = c.execute("SELECT id FROM tools WHERE name='Close CRM'").fetchone()[0]
        ads = c.execute("SELECT id FROM tools WHERE name='Acme Ads'").fetchone()[0]
    nobody = {"audience": {"everyone": False, "people": [], "teams": [], "bots": []}}
    assert api.patch(f"/api/v2/api-tools/{ads}", json=nobody, headers=headers()).status_code == 200
    cara = {"audience": {"everyone": False, "people": ["cara"], "teams": [], "bots": []}}  # she sees Close CRM
    assert api.patch(f"/api/v2/api-tools/{crm}", json=cara, headers=headers()).status_code == 200
    vault = api.app.state.vault
    with api.app.state.store.read() as c:
        cara = api.app.state.auth.identity_for_actor(c, "human:cara")
        visible = key_grants._refusal(c, vault, cara, [{"tool_id": crm}])  # pyright: ignore[reportPrivateUsage]
        hidden = key_grants._refusal(c, vault, cara, [{"tool_id": crm}, {"tool_id": ads}])  # pyright: ignore[reportPrivateUsage]
    assert (visible.status, hidden.status) == (403, 404)


def test_a_new_key_proposal_reaches_its_person_on_the_live_stream(api, monkeypatch, live):  # noqa: F811
    machine, _ = setup(api)
    put(api, "page-owners/tools", {"bot": "ops"})
    dm = post(api, "threads/dm", {"members": ["bot:ops"]})
    creds.setup(api)
    monkeypatch.setattr(api_tools, "send_check", lambda url, hdrs, params: api_tools.Reply(200))
    crm = tool(api)["id"]
    secret = {"name": "JIRA_LOGIN", "kind": "password", "env": "JIRA_LOGIN", "secret": PASSWORD, "source": "tools-chat"}
    post(api, "credentials", secret)
    post(api, f"threads/{dm['id']}/messages", {"text": "for close [saved JIRA_LOGIN for Close CRM]"})
    bot = claim(api, machine)["token"]
    stream = live("ana-test", "?topics=needs")
    ready(stream)
    with api.app.state.store.read() as c:
        before = c.execute("SELECT COALESCE(MAX(seq),0) FROM changes").fetchone()[0]
    rows = [{"key": "JIRA_LOGIN", "tool": crm, "level": "read", "bots": ["cpo"]}]
    post(api, "tools-chat/proposals", {"thread_id": dm["id"], "rows": rows}, bot)
    stream.until(lambda e: topic("needs")(e) and any(it["kind"] == "key_drop" for it in e["data"].get("items") or []))
    with api.app.state.store.read() as c:
        rows = c.execute("SELECT kind FROM changes WHERE seq>? AND topic='needs' AND subject_id='human:ana'", (before,))
        assert "key_drop" in {r[0] for r in rows}


def test_a_task_key_card_is_neither_listed_nor_saved_once_its_person_cannot_read_the_task(api, monkeypatch):
    # Cara connected Close CRM, so its Reconnect card is hers; once she may not read ops (the task's agent), the card
    # in its task's thread is not there for her.
    import json

    setup(api)
    put(api, "page-owners/tools", {"bot": "ops"})
    creds.setup(api)
    api.app.state.store.settings.credential_admins = ("ana@acme.example", "cara@acme.example")
    monkeypatch.setattr(api_tools, "send_check", lambda url, hdrs, params: api_tools.Reply(401))
    crm = post(api, "api-tools", {"name": "Close CRM"}, "cara-test")["id"]
    key(api, crm, OLD, token="cara-test")
    assert post(api, f"api-tools/{crm}/check", {}, "cara-test")["detail"] == "Key rejected"
    card = requests(api, "cara-test")["key"]["key"]
    only_ana = {"see": {"everyone": True}, "read": {"people": ["ana"]}, "write": {"people": ["ana"]}}
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE bot_config SET access=? WHERE bot='ops'", (json.dumps(only_ana),))
    assert requests(api, "cara-test") == {}
    post(api, f"credential-requests/{card['id']}/save", {"value": NEW}, "cara-test", expected=404)


def test_a_proposal_with_one_deleted_tool_lists_and_grants_the_other_row(api, monkeypatch):
    # Two keys for two tools; Acme Ads is deleted. Needs you leaves its row out, the other keeps its place (`at`), and
    # Grant takes that place, named twice or not.
    machine, _ = setup(api)
    put(api, "page-owners/tools", {"bot": "ops"})
    dm = post(api, "threads/dm", {"members": ["bot:ops"]})
    creds.setup(api)
    monkeypatch.setattr(api_tools, "send_check", lambda url, hdrs, params: api_tools.Reply(200))
    task = post(api, "tasks", {"owner": "ben", "title": "Coverage", "body": "Please."}, "ben-test")
    post(api, f"tasks/{task['id']}/asks", {"to": "ana", "line": "OK Monday", "kind": "ok"}, "ben-test")  # older
    crm = tool(api)["id"]
    ads = tool(api, "Acme Ads", base_url="https://ads.acme.example")["id"]
    for name in ("ADS_LOGIN", "JIRA_LOGIN"):
        secret = {"name": name, "kind": "password", "env": name, "secret": PASSWORD, "source": "tools-chat"}
        post(api, "credentials", secret)
    post(api, f"threads/{dm['id']}/messages", {"text": "keys [saved ADS_LOGIN] [saved JIRA_LOGIN]"})
    bot = claim(api, machine)["token"]
    rows = [
        {"key": "ADS_LOGIN", "tool": ads, "level": "read", "bots": ["cpo"]},
        {"key": "JIRA_LOGIN", "tool": crm, "level": "read", "bots": ["cpo"]},
    ]
    proposal = post(api, "tools-chat/proposals", {"thread_id": dm["id"], "rows": rows}, bot)
    first = get(api, "needs-you?limit=1")
    assert first["next"] and first["items"][0]["kind"] == "ask"
    with api.app.state.store.read() as c:
        before = c.execute("SELECT COALESCE(MAX(seq),0) FROM changes").fetchone()[0]
    assert api.delete(f"/api/v2/api-tools/{ads}", headers=headers()).status_code == 200
    with api.app.state.store.read() as c:  # its person's Needs you moved
        told = c.execute("SELECT kind FROM changes WHERE seq>? AND topic='needs' AND subject_id='human:ana'", (before,))
        assert "key_drop" in {r[0] for r in told}
    # The page after the first is not cut from the build that still had the deleted tool's row: the list starts again,
    # and the proposal on its next page has the one row left.
    after = get(api, f"needs-you?limit=1&cursor={first['next']}")
    assert after["restart"] is True
    page = get(api, f"needs-you?limit=1&cursor={after['next']}")
    [card] = [it["key_drop"] for it in page["items"] if it["kind"] == "key_drop"]
    assert [(r["at"], r["key"]) for r in card["rows"]] == [(1, "JIRA_LOGIN")]
    # A keep that does not say it names places on the card as proposed is refused, granting nothing.
    old = post(api, f"tools-chat/proposals/{proposal['id']}/grant", {"keep": [1], "seen": card["losses"]}, expected=409)
    assert old["error"]["code"] == "changed"
    # Acme Ads made again under the same id never brings its old row back, to the list or to Grant.
    assert tool(api, "Acme Ads", base_url="https://ads.acme.example")["id"] == ads
    card = requests(api, "ana-test")["key_drop"]["key_drop"]
    assert [r["at"] for r in card["rows"]] == [1]
    revived = {"keep": [0, 1], "seen": card["losses"], "positions": "original"}
    post(api, f"tools-chat/proposals/{proposal['id']}/grant", revived, expected=409)
    with api.app.state.store.read() as c:  # refused before any key was attached
        attached = c.execute("SELECT name FROM credentials WHERE tool_id IS NOT NULL AND name LIKE '%_LOGIN'")
        assert [r[0] for r in attached] == []
    body = {"keep": [1, 1], "seen": card["losses"], "positions": "original"}
    done = post(api, f"tools-chat/proposals/{proposal['id']}/grant", body)
    assert done["state"] == "granted" and [r["key"] for r in done["rows"]] == ["JIRA_LOGIN"]
