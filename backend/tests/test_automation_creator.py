"""Who made an automation (backend/automations.py automations_create): `created_by_id` is the person or bot it was made for,
and `created_via` says how it was made when not by a direct call: through Agent Ops or the Assistant, or with a personal
token. The flow keeps it server-side only: no request sets it, and an edit keeps the stored value."""

from backend.auth import Identity
from backend.store import H
from backend.tests.helpers.api import claim, get, post
from backend.tests.helpers.assistant import assistant_turn
from backend.tests.helpers.automation_api import as_bot, flow, make, patch
from backend.tests.helpers.member_bots import agent_ops, finish, turn  # noqa: F401  (fixtures)
from backend.tests.helpers.mcp import call as mcp

EVENING = {"type": "schedule", "cron": "0 18 * * *", "tz": "UTC"}
CARA_INBOX = {"type": "message", "inbox": "cara@acme.example"}
ARCHIVE = {
    "id": "sort",
    "type": "filter",
    "enabled": True,
    "rules": [
        {
            "when": [{"field": "message.subject", "op": "contains", "value": "receipt"}],
            "then": [{"do": "archive", "args": {}}],
            "stop": True,
        }
    ],
}


def as_identity(api, name, who):
    api.app.state.store.settings.test_identities[name] = who
    return name


def test_a_direct_call_names_its_person_and_no_route(api):
    made = make(api)
    assert made["created_by_id"] == "human:ana" and "created_via" not in made
    assert "created_via" not in get(api, f"automations/{made['id']}")


def test_a_bot_making_its_own_automation_is_its_creator_with_no_route(api):
    made = make(api, token=as_bot(api, "ops"), owner="bot:ops")
    assert made["created_by_id"] == "bot:ops" and "created_via" not in made


def test_the_assistant_acting_for_a_person_is_recorded(api):
    token = assistant_turn(api, "ana-test")[1]["token"]
    made = make(api, token=token)
    assert made["created_by_id"] == "human:ana" and made["created_via"] == "assistant"


def test_a_personal_token_is_recorded_labelled_or_not(api):
    labelled = as_identity(
        api, "ana-script", Identity("human:ana", "owner", "ana@acme.example", via_token=True, token_label="grok-bot")
    )
    bare = as_identity(api, "ana-bare", Identity("human:ana", "owner", "ana@acme.example", via_token=True))
    assert make(api, token=labelled)["created_via"] == "token"
    assert make(api, token=bare)["created_via"] == "token"


def test_agent_ops_acting_for_a_person_is_recorded_as_botops(api, agent_ops):
    attempt = turn(api, agent_ops, person="cara-test", text="Archive receipts from my inbox")
    err, made = mcp(
        api,
        "tico_automation_create",
        {"name": "Receipts", "owner": "person:cara", "trigger": CARA_INBOX, "steps": [ARCHIVE]},
        attempt["token"],
    )
    assert not err, made
    assert made["created_by_id"] == "human:cara" and made["created_via"] == "botops"
    assert made["enabled"] is False  # Agent Ops saves off unless the request said on
    finish(api, agent_ops, attempt)


def test_agent_ops_proposing_for_a_bot_is_recorded_with_that_bot_as_creator(api, agent_ops):
    with api.app.state.store.transaction() as c:
        H.task_create(c, "bot:finance", "Automate the receipts", "Archive receipts.", "bot:botops", lint=False)
    attempt = claim(api, agent_ops, "botops")
    draft = {"name": "Ana's receipts", "owner": "person:ana", "trigger": {"type": "event", "name": "x.y"}, "steps": []}
    err, proposed = mcp(api, "tico_automation_create", draft, attempt["token"])
    assert not err and proposed["pending"] == 1, proposed
    shown = get(api, f"automations/{proposed['id']}")
    assert shown["created_by_id"] == "bot:finance" and shown["created_via"] == "botops"


def test_an_edit_keeps_how_it_was_made_and_no_request_can_set_it(api):
    token = as_identity(
        api, "ana-script", Identity("human:ana", "owner", "ana@acme.example", via_token=True, token_label="grok-bot")
    )
    aid = make(api, token=token)["id"]
    patch(api, aid, {"created_via": "botops"}, expected=422)
    post(api, "automations", {**flow(), "created_via": "assistant"}, expected=422)
    patch(api, aid, {"name": "Renamed"})
    edited = patch(api, aid, {"trigger": EVENING, "summary": "Evenings"})  # a new version
    assert edited["version"] == 2 and edited["created_via"] == "token" and edited["created_by_id"] == "human:ana"
    assert "created_via" not in get(api, f"automations/{aid}/versions")["versions"][0]["flow"]
