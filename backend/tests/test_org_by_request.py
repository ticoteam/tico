"""Org changes by request (backend/assistant.py `instructed_org`): the Strategy Bot suggests them in words and makes one
only when the person in its chat asks, citing their message, with exactly their rights. On its own it changes nothing."""
from backend.tests.test_api import api, get, headers  # noqa: F401  (the api fixture)
from backend.tests.test_assistant import assistant_turn
from backend.tests.test_strategy_bot_chat import strategy_bot, turn
from backend.tests.test_groups import group
from backend.tests.test_groups_leads import as_bot


def cited(api, method, path, body, token, ref="turn", expected=200):
    """A write of the chat's turn; `ref` cites the person's message asking for it (None: cites nothing)."""
    extra = {"X-Tico-On-Behalf-Of": ref} if ref else {}
    r = api.request(method, "/api/v2/" + path, json=body, headers={**headers(token), **extra})
    assert r.status_code == expected, r.text
    return r.json()


def test_the_strategy_bot_makes_the_org_change_the_owner_asked_for(api):
    strategy_bot(api)
    _, attempt = turn(api, "coo", "ana-test", "Make a Research group with ops leading it, and Ben reports to me")
    token = attempt["token"]
    # Without citing what they said it changes nothing.
    refused = cited(api, "POST", "groups", {"name": "Research"}, token, ref=None, expected=403)
    assert refused["error"]["code"] == "confirm_required"
    gid = cited(api, "POST", "groups", {"name": "Research", "add": {"bots": ["ops"]}}, token)["id"]
    cited(api, "PATCH", f"groups/{gid}", {"name": "Lab", "lead": "bot:ops"}, token)
    assert (group(api, gid)["name"], group(api, gid)["lead"]) == ("Lab", "bot:ops")
    cited(api, "POST", "humans/ben", {"reports_to": "ana"}, token)
    revision = next(b for b in get(api, "bots") if b["slug"] == "ops")["revision"]
    cited(api, "POST", "bots/ops/definition", {"reports_to": "cpo", "expected_revision": revision}, token)
    org = get(api, "org")
    assert next(p for p in org["people"] if p["id"] == "ben")["reports_to"] == "ana"
    assert next(b for b in get(api, "bots") if b["slug"] == "ops")["reports_to"] == "cpo"
    cited(api, "DELETE", f"groups/{gid}", None, token)
    assert gid not in {row["id"] for row in get(api, "groups")}
    # Only who reports to whom rides on their words; anything else on the profile is still a card.
    assert cited(api, "POST", "humans/ben", {"reports_to": "ana", "title": "CTO"}, token,
                 expected=403)["error"]["code"] == "confirm_required"


def test_on_its_own_the_strategy_bot_changes_no_group(api):
    strategy_bot(api)
    as_bot(api, "goal-manager", "gm-test")
    cited(api, "POST", "groups", {"name": "Research"}, "gm-test", ref=None, expected=403)
    assert "Research" not in {row["name"] for row in get(api, "groups")}


def test_a_member_asking_their_assistant_gets_only_their_own_rights(api):
    _, attempt = assistant_turn(api, "cara-test", "Make a Research group")
    refused = cited(api, "POST", "groups", {"name": "Research"}, attempt["token"], expected=403)
    assert refused["error"]["code"] == "forbidden"
