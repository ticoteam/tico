"""`tools/list` offers each caller the tools it can use, by role and kind; a hidden tool is refused if it is called anyway."""

from backend.tests.test_agents import credential, hermes_bot
from backend.tests.test_api import api, assign, claim, get, headers, post, ready, runner, setup_attempt  # noqa: F401
from backend.tests.test_member_bots import botops, turn  # noqa: F401
from backend.tests.test_mcp import call, rpc
from clients import hubtools

BOTOPS_ONLY = {"hub_credential_request", "hub_credential_set", "hub_message_redact",
               "hub_support_file"}
HUMANS_ONLY = {"hub_brief", "hub_bot_recent", "hub_needs_you_start", "hub_needs_you_commit", "hub_proposal_decide",
               "hub_update_reply", "hub_assistant_read", "hub_assistant_send"}
BOTS_ONLY = {"hub_update_create", "hub_file_publish", "hub_file_link"}
ASSIGNMENT_TOOLS = {"hub_bot_assignment_list", "hub_bot_assignment_policy", "hub_bot_assignment_create",
                    "hub_bot_assignment_update", "hub_bot_assignment_cleanup"}


def offered(api, token):
    return {t["name"] for t in rpc(api, "tools/list", token=token)["result"]["tools"]}


def test_each_caller_is_offered_only_the_tools_it_can_use(api, botops):
    hermes_bot(api)
    lists = {
        "owner": offered(api, "ana-test"),
        "admin": offered(api, "ben-test"),
        "member": offered(api, "cara-test"),
        "bot": offered(api, setup_attempt(api, "ops")[2]["token"]),
        "botops": offered(api, turn(api, botops, person="ana-test")["token"]),
        "agent": offered(api, credential(api)["token"]),
    }
    me = get(api, "me", token="cara-test")
    assert me["kind"] == "member" and get(api, "me", token="ben-test")["kind"] == "admin"
    everything = {n for n, t in hubtools.BY_NAME.items() if not t["local"]}
    for who, names in lists.items():
        assert names < everything, who                                   # nobody is offered every tool
        assert "hub_assistant_propose" not in names, who                 # the Assistant's own
        assert {"hub_whoami", "hub_task_list", "hub_message_send", "hub_doc_search", "hub_sql"} <= names, who
    for who in ("owner", "admin", "member"):
        assert HUMANS_ONLY <= lists[who] and not BOTS_ONLY & lists[who] and not BOTOPS_ONLY & lists[who], who
        assert ASSIGNMENT_TOOLS <= lists[who], who
    for who in ("bot", "agent"):
        assert BOTS_ONLY <= lists[who] and not HUMANS_ONLY & lists[who] and not BOTOPS_ONLY & lists[who], who
        assert (ASSIGNMENT_TOOLS - {"hub_bot_assignment_policy"}) <= lists[who], who
        assert "hub_bot_assignment_policy" not in lists[who], who
    assert "hub_grokbot_sync" in lists["owner"] and "hub_grokbot_sync" in lists["admin"]
    assert "hub_grokbot_sync" not in lists["member"]
    assert {"hub_api", "hub_bot_update"} <= lists["member"]
    assert BOTOPS_ONLY <= lists["botops"] and BOTS_ONLY <= lists["botops"] and not HUMANS_ONLY & lists["botops"]
    assert (ASSIGNMENT_TOOLS - {"hub_bot_assignment_policy"}) <= lists["botops"]
    assert "hub_bot_assignment_policy" not in lists["botops"]
    assert lists["bot"] < lists["owner"] | lists["bot"] and len(lists["bot"]) < len(lists["botops"])


def test_a_tool_that_is_not_offered_is_refused_when_it_is_called(api, botops):
    token = setup_attempt(api, "ops")[2]["token"]
    err, out = call(api, "hub_api", {"method": "GET", "path": "me"}, token=token)
    assert err and out["error"] == "forbidden" and "not available" in out["detail"]
    err, out = call(api, "hub_needs_you_start", {}, token=token)
    assert err and out["error"] == "forbidden"
    err, out = call(api, "hub_bot_update", {"slug": "ops", "status": "paused"}, token="cara-test")
    assert err and out["error"] == "forbidden"
    err, out = call(api, "hub_update_create", {"body": "- Did a thing"}, token="ana-test")
    assert err and out["error"] == "forbidden"
    # And the server still refuses what the list does not: a bot cannot register a bot however the call is made.
    r = api.post("/api/v2/bots/register", json={"slug": "x", "display_name": "X"}, headers=headers(token))
    assert r.status_code == 403


def test_the_assistant_is_offered_reads_its_own_writes_and_its_proposal_tool():
    names = {t["name"] for t in hubtools.listing(kind="assistant")}
    assert "hub_assistant_propose" in names and "hub_task_create" in names and "hub_health_check" in names
    assert not {"hub_sql", "hub_api", "hub_message_send", "hub_update_create", "hub_bot_update"} & names
    assert not ASSIGNMENT_TOOLS & names
    assert {t["name"] for t in hubtools.listing()} >= names                # no kind: everything, the server decides
