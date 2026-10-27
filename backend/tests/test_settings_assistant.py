"""The Assistant changes settings with the same catalogue and classes as BotOps (backend/settings_catalog.py,
backend/assistant.py): a direct one on the person's words in their own chat, a confirm one as a Confirm card there, a
person-only one never; the owner's "Assistant acts without asking" off makes every change a card.

Ana owns the company, Ben is an admin, Cara is a member (the base fixture).
"""
import re

import pytest

from backend import settings_catalog
from backend.assistant import valid_operation
from backend.auth import Identity
from backend.settings_entries import ENTRIES
from backend.store import Problem
from backend.tests.test_api import api, as_member, get, headers, post, put  # noqa: F401  (fixture)
from backend.tests.test_assistant import assistant_turn
from clients import hubtools
from clients.tico import APIError


def mcp(api, name, args, token):
    """A `hub` tool in the Assistant's run, answered by this server: `(error, payload)` as the MCP helper gives."""
    class Turn:
        def call(self, method, path, body=None, key=None, query=None, delegate=False):
            extra = {"X-Tico-On-Behalf-Of": delegate if isinstance(delegate, str) else "turn"} if delegate else {}
            r = api.request(method, "/api/v2/" + path, json=body, params=query, headers={**headers(token), **extra})
            if r.status_code >= 400:
                raise APIError(r.json()["error"]["code"], r.json()["error"]["detail"], status=r.status_code)
            return r.json()

        def get(self, path, **query):
            return self.call("GET", path, query=query)

        def post(self, path, body=None, key=None):
            return self.call("POST", path, body or {}, key)
    try:
        return False, hubtools.BY_NAME[name]["fn"](Turn(), args)
    except APIError as refused:
        return True, {"error": refused.code, "detail": str(refused)}


def release_times(api):
    return get(api, "release-schedule")["times"]


def test_a_person_asks_their_assistant_for_a_direct_setting_and_it_changes(api):
    before = release_times(api)
    r, attempt = assistant_turn(api, "ana-test", "Set the release to 09:30")
    token = attempt["token"]
    assert "hub_settings_set" in {t["name"] for t in hubtools.listing(kind="assistant")}
    # Without citing the person's words it is a card, and nothing changes yet.
    err, card = mcp(api, "hub_settings_set", {"key": "release.times", "value": "09:30"}, token)
    assert not err and card["needs_confirm"] is True, card
    assert release_times(api) == before
    # On their words, it changes at once and the reply has the new value.
    err, done = mcp(api, "hub_settings_set", {"key": "release.times", "value": "09:30", "on_behalf_of": "turn"}, token)
    assert not err and done["value"] == ["09:30"], done
    assert release_times(api) == ["09:30"]
    with api.app.state.store.read() as c:
        row = c.execute("SELECT actor,detail_json FROM events WHERE detail_json LIKE '%\"via\": \"assistant\"%' "
                        "AND detail_json LIKE '%\"cited\"%' ORDER BY rowid DESC LIMIT 1").fetchone()
    assert row and row["actor"] == "human:ana", row


def test_a_member_changes_a_direct_setting_anyone_may_as_themselves(api):
    as_member(api, "cara@acme.example")
    r, attempt = assistant_turn(api, "cara-test", "Make me an incoming trigger called Orders")
    err, done = mcp(api, "hub_settings_set", {"key": "webhooks.incoming.create", "value": {"name": "Orders"},
                                              "on_behalf_of": "turn"}, attempt["token"])
    assert not err and done["value"] == {"name": "Orders"}, done
    with api.app.state.store.read() as c:
        assert c.execute("SELECT created_by FROM triggers WHERE name='Orders'").fetchone()[0] == "human:cara"


def test_a_confirm_setting_is_a_card_in_the_assistant_chat_and_the_tap_changes_it(api):
    r, attempt = assistant_turn(api, "ana-test", "Stop admins seeing SQL")
    err, card = mcp(api, "hub_settings_set", {"key": "team.rules.admin_sql", "value": "off", "on_behalf_of": "turn"},
                    attempt["token"])
    assert not err and card["needs_confirm"] is True, card
    assert get(api, "access")["rules"]["admin_sql"] is True
    action = card["action"]
    assert action["owner"] == "human:ana" and action["status"] == "pending"
    with api.app.state.store.read() as c:
        row = c.execute("SELECT proposer,conversation_id FROM assistant_actions WHERE id=?", (action["id"],)).fetchone()
        assert row["proposer"] == "assistant"
        room = c.execute("SELECT owner_actor FROM conversations WHERE id=?", (row["conversation_id"],)).fetchone()
        assert room["owner_actor"] == "human:ana"                # the person's own chat, not BotOps'
    # The Assistant can't tap it; the person's own tap runs it, as them.
    post(api, f"assistant/actions/{action['id']}/confirm", {}, token=attempt["token"], expected=403)
    done = post(api, f"assistant/actions/{action['id']}/confirm", {}, token="ana-test")["action"]
    assert done["status"] == "done", done
    assert get(api, "access")["rules"]["admin_sql"] is False


def test_with_assistant_acts_without_asking_off_a_direct_setting_is_a_card(api):
    put(api, "access/rules", {"assistant_direct": False})
    before = release_times(api)
    r, attempt = assistant_turn(api, "ana-test", "Set the release to 09:30")
    token = attempt["token"]
    err, card = mcp(api, "hub_settings_set", {"key": "release.times", "value": "09:30", "on_behalf_of": "turn"}, token)
    assert not err and card["needs_confirm"] is True, card
    assert release_times(api) == before
    # Nor does the write itself run on the person's words while the rule is off.
    refused = api.patch("/api/v2/release-schedule", json={"times": ["09:30"]},
                        headers={**headers(token), "X-Tico-On-Behalf-Of": "turn"})
    assert refused.status_code == 403 and refused.json()["error"]["code"] == "confirm_required", refused.text
    post(api, f"assistant/actions/{card['action']['id']}/confirm", {}, token="ana-test")
    assert release_times(api) == ["09:30"]


def test_a_member_asking_for_an_admin_setting_changes_nothing_and_is_told_who_can(api):
    as_member(api, "cara@acme.example")
    before = release_times(api)
    r, attempt = assistant_turn(api, "cara-test", "Set the release to 09:30")
    token = attempt["token"]
    err, refused = mcp(api, "hub_settings_set", {"key": "release.times", "value": "09:30", "on_behalf_of": "turn"},
                       token)
    assert err and "owner or an admin" in str(refused), refused
    # The same write sent without asking for its plan meets the same check.
    raw = api.patch("/api/v2/release-schedule", json={"times": ["09:30"]},
                    headers={**headers(token), "X-Tico-On-Behalf-Of": "turn"})
    assert raw.status_code == 403 and "owner or an admin" in raw.json()["error"]["detail"], raw.text
    assert release_times(api) == before
    with api.app.state.store.read() as c:
        assert not c.execute("SELECT 1 FROM assistant_actions WHERE owner='human:cara'").fetchone()


def test_the_assistant_never_runs_two_settings_at_once_nor_cards_a_person_only_one(api):
    r, attempt = assistant_turn(api, "ana-test", "Set the release to 09:30 and turn it off")
    token = attempt["token"]
    both = api.patch("/api/v2/release-schedule", json={"times": ["09:30"], "on": False},
                     headers={**headers(token), "X-Tico-On-Behalf-Of": "turn"})
    assert both.status_code == 403 and both.json()["error"]["code"] == "confirm_required", both.text
    rules = api.put("/api/v2/access/rules", json={"admin_sql": False},
                    headers={**headers(token), "X-Tico-On-Behalf-Of": "turn"})       # a confirm one: a card
    assert rules.status_code == 403 and rules.json()["error"]["code"] == "confirm_required", rules.text
    # A card holds one catalogue setting that is not the person's own to change in Settings.
    post(api, "assistant/actions", {"summary": "Gateway key", "method": "PUT", "path": "/api/v2/providers/gateway",
                                    "body": {"key": "x"}}, token=token, expected=422)
    post(api, "assistant/actions", {"summary": "Two rules", "method": "PUT", "path": "/api/v2/access/rules",
                                    "body": {"admin_sql": False, "assistant_direct": False}}, token=token, expected=422)
    card = post(api, "assistant/actions", {"summary": "Admins see SQL", "method": "PUT", "path": "/api/v2/access/rules",
                                           "body": {"admin_sql": False}}, token=token)["action"]
    assert card["status"] == "pending"


# ------------------------------------------------------------------ the same request to BotOps and to the Assistant
SAMPLE = {"bool": "on", "int": "3", "number": "1", "list": "a", "text": "x", "json": {"name": "x"}, "upload": "x"}


def sample(e):
    return e.value.split(":", 1)[1].split("|")[0] if e.value.startswith("choice:") else SAMPLE[e.value]


def outcome(c, auth, who, e, card_direct=False):
    target = {"bot": "ops", "computer": "x", "person": who.actor.split(":", 1)[1], "id": "x"}
    proposer = "assistant" if who.via == "assistant" else "botops"
    try:
        made = settings_catalog.plan(c, auth, who, e, sample(e), target, propose=lambda *a: {"needs_confirm": True},
                                     card_direct=card_direct,
                                     can_card=lambda method, path: valid_operation(method, path, None, proposer))
    except Problem as refused:
        return refused.code, refused.status, refused.detail
    return "run" if "run" in made else "card"


@pytest.mark.parametrize("person,role", [("ana", "owner"), ("ben", "human"), ("cara", "human")])
def test_botops_and_the_assistant_give_every_setting_the_same_class_and_who_may(api, person, role):
    as_member(api, "cara@acme.example")
    auth = api.app.state.auth
    email = person + "@acme.example"
    botops = Identity("human:" + person, role, email=email, via="botops")
    assistant = Identity("human:" + person, role, email=email, via="assistant", task_actor="bot:coo")
    seen = set()
    with api.app.state.store.read() as c:
        for e in ENTRIES:
            got = outcome(c, auth, assistant, e)
            assert got == outcome(c, auth, botops, e), e.key
            seen.add(got if isinstance(got, str) else got[0])
            # Cards for a direct entry (the owner's rule off): a card wherever BotOps can make one, else the link.
            if got == "run" and settings_catalog.kind_for(e, {}) == "direct":
                carded = outcome(c, auth, assistant, e, card_direct=True)
                path = "/api/v2/" + re.sub(r"\{\w+\}", "x", e.route)
                assert carded == ("card" if valid_operation(e.method, path, None, "botops") else carded), e.key
                assert carded == "card" or carded[0] == "unavailable", (e.key, carded)
    assert {"run", "card", "person_only"} <= seen or role == "human", seen


def test_the_assistant_may_card_every_catalogue_route_botops_may():
    for e in ENTRIES:
        path = "/api/v2/" + re.sub(r"\{\w+\}", "x", e.route)
        if e.kind != "person":
            assert valid_operation(e.method, path, None, "assistant") == valid_operation(e.method, path, None, "botops"), e.key
