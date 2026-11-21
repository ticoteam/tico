"""Needs you is the one queue (master plan P1): it lists the person's open access requests, key cards and key-drop
proposals, each through its own list and authority check, and only to someone who may settle it. Ana owns the company
and Ben is an admin (both credential administrators); Cara is a member. Ops owns the Tools page."""

from backend import api_tools
from backend.tests.helpers import credentials as creds
from backend.tests.helpers.api import claim, get, post, put
from backend.tests.helpers.api_tools import key, tool
from backend.tests.helpers.chat_goals import setup

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
    post(api, f"access-requests/{rid}/grant", {}, "cara-test", expected=403)
    post(api, f"credential-requests/{mine['key']['key']['id']}/save", {"value": NEW}, "cara-test", expected=404)
    post(api, f"tools-chat/proposals/{mid}/grant", {"keep": [0]}, "cara-test", expected=403)

    # Ben's Read only settles every copy: Ana's list drops it too, and cpo reads Acme Ads, no more.
    assert post(api, f"access-requests/{rid}/grant", {"level": "read"}, "ben-test")["state"] == "granted"
    with api.app.state.store.read() as c:
        assert api_tools.subject_level(c, ads, "bot:cpo") == "read"
    assert post(api, f"credential-requests/{mine['key']['key']['id']}/save", {"value": NEW})["status"] == "saved"
    assert post(api, f"tools-chat/proposals/{mid}/grant", {"keep": [0]})["state"] == "granted"
    assert requests(api, "ana-test") == {} and requests(api, "ben-test") == {}
