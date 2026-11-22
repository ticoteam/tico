"""Who made an automation (backend/automations.py automations_create): `created_by_id` is the principal the call was made
as, and `created_via` says how it was made when not by a direct call: through Agent Ops or the Assistant, or with a
personal token. The flow keeps it server-side only: no request sets it, and an edit keeps the stored value. Agent Ops'
own cases (saves for a person, proposals for a bot) are asserted in test_agent_ops_automations.py."""

from backend.auth import Identity
from backend.tests.helpers.api import get, post
from backend.tests.helpers.assistant import assistant_turn
from backend.tests.helpers.automation_api import as_bot, flow, make, patch

EVENING = {"type": "schedule", "cron": "0 18 * * *", "tz": "UTC"}


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


def test_a_page_chat_identity_is_recorded_with_no_route(api):
    token = as_identity(
        api, "ana-page", Identity("human:ana", "owner", "ana@acme.example", via="page_chat", confirmed=True)
    )
    made = make(api, token=token)
    assert made["created_by_id"] == "human:ana" and "created_via" not in made


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
    back = post(api, f"automations/{aid}/versions/1/restore", {})
    assert back["created_via"] == "token" and back["created_by_id"] == "human:ana"
