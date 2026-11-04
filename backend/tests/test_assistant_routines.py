"""Inbox routines from chat stay off until the person's card click; the reviewed plan is what runs."""
import copy

import pytest

from backend.tests.test_api import api, get, headers, post  # noqa: F401
from backend.tests.test_assistant import assistant_turn
from backend.tests.test_automation_templates import fill


def flow(mode=None):
    choose = {"to": ["customers", "later"], "default": "customers"}
    if mode:
        choose["mode"] = mode
    return {"name": "Customer mail", "owner": "person:ana",
            "trigger": {"type": "message", "inbox": "ana@acme.example"},
            "destinations": [
                {"id": "customers", "name": "Customers", "then": [{"do": "leave_for_you", "args": {}}]},
                {"id": "later", "name": "Read later", "then": [{"do": "label", "args": {"name": "Read later"}}]}],
            "steps": [{"id": "route", "type": "llm", "as": "assistant:ana", "out": "answer",
                       "prompt": "Draft a reply to customers. Newsletters go to Read later with no reply.",
                       "choose": choose}]}


def turn(api, text="Draft replies to new customer emails; newsletters go to Read later"):
    return assistant_turn(api, "ana-test", text)[1]["token"]


def propose(api, token, body, path="/api/v2/automations", method="POST", expected=200):
    return post(api, "assistant/actions", {"summary": "Handle customer mail", "method": method,
                                          "path": path, "body": body}, token=token, expected=expected)


def confirm(api, action, token="ana-test", expected=200):
    return post(api, f"assistant/actions/{action['id']}/confirm", {}, token=token, expected=expected)


def test_create_saves_off_with_a_card_and_only_its_person_can_turn_it_on(api):
    token = turn(api)
    made = post(api, "automations", flow(), token=token)
    assert made["enabled"] is False and made["needs_confirm"] is True
    assert made["steps"][0]["choose"]["mode"] == "draft"
    card = made["action"]
    assert card["status"] == "pending" and card["body"]["enabled"] is True
    assert card["path"] == "/api/v2/automations/" + made["id"]
    assert get(api, "automations/" + made["id"])["enabled"] is False
    confirm(api, card, token=token, expected=403)
    confirm(api, card, token="ben-test", expected=404)
    assert confirm(api, card)["action"]["status"] == "done"
    assert get(api, "automations/" + made["id"])["enabled"] is True
    confirm(api, card, expected=409)


@pytest.mark.parametrize("template", ["founder", "sales", "support", "blank"])
def test_existing_templates_save_off_and_get_readable_cards(api, template):
    token = turn(api)
    given = fill(api, template, "person:ana", "ana@acme.example", token)
    made = post(api, "automations", given, token=token)
    assert made["enabled"] is False
    lines = {line["field"]: line["new"] for line in made["action"]["diff"]}
    assert lines["Inbox"] == "ana@acme.example"
    assert lines["Mode"] == ("Sort" if template == "blank" else "Draft")
    assert all(dest["name"] in lines for dest in made["destinations"])
    assert all(isinstance(value, str) for value in lines.values())
    assert confirm(api, made["action"])["action"]["status"] == "done"


def test_post_can_be_proposed_directly_and_creates_only_on_confirmation(api):
    token = turn(api)
    card = propose(api, token, {**flow(), "enabled": False})["action"]
    assert card["body"]["enabled"] is True
    assert card["body"]["steps"][0]["choose"]["mode"] == "draft"
    with api.app.state.store.read() as c:
        assert not c.execute("SELECT id FROM automations WHERE name='Customer mail'").fetchone()
    assert confirm(api, card)["action"]["status"] == "done"
    with api.app.state.store.read() as c:
        assert c.execute("SELECT enabled FROM automations WHERE name='Customer mail'").fetchone()[0] == 1


def test_summary_shows_the_inbox_destinations_and_mode_not_the_bots_summary(api):
    made = post(api, "automations", flow(), token=turn(api))
    card = made["action"]
    assert card["description"] == 'Turn on routine “Customer mail”'
    assert [(d["field"], d["new"]) for d in card["diff"]] == [
        ("Inbox", "ana@acme.example"), ("Mode", "Draft"),
        ("Customers", "Leave in inbox"), ("Read later", "Label Read later")]


def test_cancel_leaves_the_saved_routine_off(api):
    made = post(api, "automations", flow(), token=turn(api))
    post(api, f"assistant/actions/{made['action']['id']}/cancel", {})
    assert get(api, "automations/" + made["id"])["enabled"] is False
    confirm(api, made["action"], expected=409)


def test_chat_cannot_enable_edit_run_or_restore_a_message_routine_without_a_card(api):
    saved = post(api, "automations", {**flow("draft"), "enabled": False})
    token = turn(api, "Turn it on and change the destination")
    path = "/api/v2/automations/" + saved["id"]
    for method, target, body in [
            ("POST", "/api/v2/automations", {**flow("draft"), "enabled": True}),
            ("PATCH", path, {"enabled": True}), ("PATCH", path, {"name": "Changed"}),
            ("POST", path + "/run", {}), ("POST", path + "/versions/1/restore", {})]:
        r = api.request(method, target, json=body, headers=headers(token))
        assert r.status_code == 403 and r.json()["error"]["code"] == "confirm_required", r.text
    updated = copy.deepcopy(saved["destinations"])
    updated[1]["name"] = "Newsletters"
    card = propose(api, token, {"destinations": updated, "enabled": True}, path, "PATCH")["action"]
    assert get(api, "automations/" + saved["id"])["destinations"][1]["name"] == "Read later"
    assert confirm(api, card)["action"]["status"] == "done"
    current = get(api, "automations/" + saved["id"])
    assert current["enabled"] and current["destinations"][1]["name"] == "Newsletters"
    # Pausing is always available, but undoing that pause cannot enable it on chat words.
    r = api.patch(path, json={"enabled": False}, headers=headers(token))
    assert r.status_code == 200 and not r.json()["enabled"], r.text
    changes = get(api, "assistant/changes", token)["changes"]
    paused = next(change for change in changes if change["object"] == "automation:" + saved["id"])
    refused = post(api, f"assistant/changes/{paused['id']}/restore", {}, token=token, expected=403)
    assert refused["error"]["code"] == "confirm_required"


def test_reply_requires_send_permission_on_proposal_and_again_at_confirmation(api):
    token = turn(api, "Automatically reply to customers")
    path = "/api/v2/inboxes/ana@acme.example"
    assert api.patch(path, json={"send": False}, headers=headers()).status_code == 200
    refused = propose(api, token, flow("reply"), expected=403)
    assert refused["error"]["code"] == "send_permission"
    # Saving Draft remains possible while sending is disabled.
    draft = post(api, "automations", flow(), token=token)
    assert draft["steps"][0]["choose"]["mode"] == "draft"
    assert api.patch(path, json={"send": True}, headers=headers()).status_code == 200
    reply = post(api, "automations", flow("reply"), token=token)
    assert reply["enabled"] is False
    assert {d["field"]: d["new"] for d in reply["action"]["diff"]}["Mode"] == "Reply"
    assert api.patch(path, json={"send": False}, headers=headers()).status_code == 200
    decided = confirm(api, reply["action"])["action"]
    assert decided["status"] == "failed" and decided["result"]["status_code"] == 403
    assert get(api, "automations/" + reply["id"])["enabled"] is False


def test_stale_card_does_not_approve_a_changed_flow(api):
    made = post(api, "automations", flow(), token=turn(api))
    changed = api.patch("/api/v2/automations/" + made["id"], json={"name": "Changed elsewhere"}, headers=headers())
    assert changed.status_code == 200
    result = confirm(api, made["action"])["action"]
    assert result["status"] == "failed" and result["result"]["status_code"] == 409
    saved = get(api, "automations/" + made["id"])
    assert saved["enabled"] is False and saved["name"] == "Changed elsewhere"


def test_reply_can_be_approved_but_company_send_permission_is_also_required(api):
    token = turn(api, "Automatically send replies to customers")
    made = post(api, "automations", flow("reply"), token=token)
    assert confirm(api, made["action"])["action"]["status"] == "done"
    assert get(api, "automations/" + made["id"])["enabled"] is True
    changed = api.put("/api/v2/google/permissions", json={"organize_mail": True, "send_mail": False,
                                                       "change_calendar": True}, headers=headers())
    assert changed.status_code == 200, changed.text
    refused = propose(api, token, flow("reply"), expected=403)
    assert refused["error"]["code"] == "send_permission"


def test_a_reply_routine_can_be_stopped_on_a_card_after_send_permission_is_revoked(api):
    saved = post(api, "automations", flow("reply"))
    token = turn(api, "Stop the routine")
    assert api.patch("/api/v2/inboxes/ana@acme.example", json={"send": False}, headers=headers()).status_code == 200
    card = propose(api, token, {"enabled": False}, "/api/v2/automations/" + saved["id"], "PATCH")["action"]
    assert confirm(api, card)["action"]["status"] == "done"
    assert get(api, "automations/" + saved["id"])["enabled"] is False


def test_proposal_checks_inbox_access_before_showing_a_plan(api):
    given = flow()
    given["trigger"]["inbox"] = "ben@acme.example"
    assert propose(api, turn(api), given, expected=403)["error"]["code"] == "forbidden"


def test_restoring_a_message_version_needs_a_card_even_after_the_trigger_changed(api):
    saved = post(api, "automations", flow("draft"))
    path = "/api/v2/automations/" + saved["id"]
    changed = api.patch(path, json={"trigger": {"type": "event", "name": "test.event"}}, headers=headers())
    assert changed.status_code == 200, changed.text
    refused = api.post(path + "/versions/1/restore", json={}, headers=headers(turn(api, "Restore the old routine")))
    assert refused.status_code == 403 and refused.json()["error"]["code"] == "confirm_required", refused.text


def test_bulk_card_freezes_and_describes_message_edits_too(api):
    saved = post(api, "automations", {**flow("draft"), "enabled": False})
    token = turn(api)
    card = propose(api, token, {"actions": [{"method": "PATCH",
        "path": "/api/v2/automations/" + saved["id"], "body": {"enabled": True}}]},
        "/api/v2/assistant/batch")["action"]
    text = " ".join(line["new"] for line in card["diff"])
    assert "ana@acme.example" in text and "Read later" in text and "Draft" in text
    assert confirm(api, card)["action"]["status"] == "done"
    assert get(api, "automations/" + saved["id"])["enabled"] is True
