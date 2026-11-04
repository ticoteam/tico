"""BotOps operates automations (backend/automations.py, backend/botops_act.py): asked by a person it has that person's
rights and saves, edits, pauses, runs, restores and deletes as they could; what it makes is off unless the request
said on. Asked by a bot it has only that bot's rights, so where the bot may not change things it proposes: a pending
version a person turns on. Ana owns the company, Cara is a member (the base fixture).
"""
from backend import botops_act
from backend.store import H
from backend.tests.test_api import api, claim, get, headers, post  # noqa: F401  (fixture)
from backend.tests.test_automation_api import NOTE_STEP, audit_rows
from backend.tests.test_member_bots import botops, finish, turn  # noqa: F401  (fixtures)
from backend.tests.test_mcp import call as mcp

CARA_INBOX = {"type": "message", "inbox": "cara@acme.example"}
ARCHIVE = {"id": "sort", "type": "filter", "enabled": True, "rules": [
    {"when": [{"field": "message.subject", "op": "contains", "value": "receipt"}], "then": [{"do": "archive", "args": {}}],
     "stop": True}]}


LABEL = {"id": "tag", "type": "action", "enabled": True, "do": "label", "args": {"name": "Receipts"}}


def flow(**extra):
    return {"name": "Receipts", "owner": "person:cara", "trigger": CARA_INBOX, "steps": [ARCHIVE], **extra}


def test_a_person_asks_and_botops_saves_off_unless_asked_on(api, botops):
    attempt = turn(api, botops, person="cara-test", text="Archive receipts from my inbox")
    err, made = mcp(api, "hub_automation_create", flow(), attempt["token"])
    assert not err, made
    assert made["enabled"] is False and made["can_edit"] is True and made["owner_id"] == "person:cara"
    assert [(v["n"], v["status"]) for v in get(api, f"automations/{made['id']}/versions", "cara-test")["versions"]] == [
        (1, "active")]
    rows = audit_rows(api, made["id"])
    assert [(r["actor"], r["via"]) for r in rows] == [("human:cara", "botops")]
    finish(api, botops, attempt)
    attempt = turn(api, botops, person="cara-test", text="Archive receipts from my inbox and turn it on")
    err, on = mcp(api, "hub_automation_create", flow(enabled=True), attempt["token"])
    assert not err and on["enabled"] is True, on


def test_a_persons_edits_are_applied_as_they_could(api, botops):
    """Update, pause, run and restore go straight through, with no card and no pending version."""
    aid = post(api, "automations", flow(enabled=True), "cara-test")["id"]
    attempt = turn(api, botops, person="cara-test", text="Label receipts first")
    err, edited = mcp(api, "hub_automation_update", {"id": aid, "steps": [LABEL, ARCHIVE], "summary": "Label first"},
                      attempt["token"])
    assert not err and edited["version"] == 2 and "pending" not in edited, edited
    assert get(api, f"automations/{aid}/versions?status=pending", "cara-test")["versions"] == []
    err, paused = mcp(api, "hub_automation_update", {"id": aid, "enabled": False}, attempt["token"])
    assert not err and paused["enabled"] is False, paused
    err, ran = mcp(api, "hub_automation_run", {"id": aid, "input": {"message": {"subject": "hello"}}}, attempt["token"])
    assert not err, ran
    err, back = mcp(api, "hub_automation_restore", {"id": aid, "n": 1}, attempt["token"])
    assert not err and back["version"] == 3 and len(back["steps"]) == 1, back
    for answer in (edited, paused, ran, back):
        assert "needs_confirm" not in answer                                  # nothing waited on a card
    for method, path in (("POST", "automations"), ("PATCH", f"automations/{aid}"), ("POST", f"automations/{aid}/run"),
                         ("POST", f"automations/{aid}/versions/1/restore"), ("DELETE", f"automations/{aid}/versions/2")):
        assert botops_act.classify(method, path) == "do" and not botops_act.card_for_person(method, path), path
    assert {r["actor"] for r in audit_rows(api, aid)} == {"human:cara"}


def test_a_persons_delete_waits_for_their_own_tap(api, botops):
    """Deleting an automation is a Confirm card in the person's BotOps chat, as deleting a routine is."""
    aid = post(api, "automations", flow(enabled=True), "cara-test")["id"]
    attempt = turn(api, botops, person="cara-test", text="Delete my receipts automation")
    err, card = mcp(api, "hub_automation_delete", {"id": aid}, attempt["token"])
    assert not err and card["needs_confirm"], card
    assert get(api, f"automations/{aid}", "cara-test")["id"] == aid          # nothing changed yet
    for method, path in (("POST", f"automations/{aid}/delete"), ("DELETE", f"automations/{aid}")):
        assert botops_act.classify(method, path) == "confirm" and botops_act.card_for_person(method, path), path
        assert botops_act.card_summary(method, path) == "Delete an automation"
    post(api, f"assistant/actions/{card['action']['id']}/confirm", {}, token=attempt["token"], expected=403)
    assert post(api, f"assistant/actions/{card['action']['id']}/confirm", {}, token="cara-test")["action"]["status"] == "done"
    get(api, f"automations/{aid}", "cara-test", expected=404)


def test_botops_never_goes_past_the_askers_rights(api, botops):
    anas = post(api, "automations", flow(owner="person:ana", trigger={"type": "message", "inbox": "ana@acme.example"}))
    attempt = turn(api, botops, person="cara-test", text="Draft replies to everything in Ana's inbox")
    err, refused = mcp(api, "hub_automation_create", flow(trigger={"type": "message", "inbox": "ana@acme.example"}),
                       attempt["token"])
    assert err, refused                                                       # Cara may not read Ana's mail
    err, refused = mcp(api, "hub_automation_create", flow(owner="person:ana"), attempt["token"])
    assert err, refused                                                       # nor make Ana one
    err, refused = mcp(api, "hub_automation_update", {"id": anas["id"], "steps": []}, attempt["token"])
    assert err, refused                                                       # nor edit Ana's, or propose to it
    err, refused = mcp(api, "hub_automation_delete", {"id": anas["id"]}, attempt["token"])
    assert err, refused
    assert get(api, f"automations/{anas['id']}/versions")["versions"][0]["status"] == "active"
    assert len(get(api, f"automations/{anas['id']}/versions")["versions"]) == 1
    assert [a["id"] for a in get(api, "automations", "cara-test")["automations"] if not a["builtin"]] == []


def test_a_bot_asks_and_botops_proposes_where_that_bot_may_not_change(api, botops):
    anas = post(api, "automations", flow(owner="person:ana", trigger={"type": "event", "name": "x.y"}))["id"]
    with api.app.state.store.transaction() as c:
        H.task_create(c, "bot:finance", "Automate the receipts", "Archive receipts.", "bot:botops", lint=False)
    attempt = claim(api, botops, "botops")
    draft = {"name": "Ana's receipts", "owner": "person:ana", "trigger": {"type": "event", "name": "x.y"}, "steps": []}
    err, proposed = mcp(api, "hub_automation_create", draft, attempt["token"])
    assert not err and proposed["pending"] == 1 and proposed["can_edit"] is False, proposed
    shown = get(api, f"automations/{proposed['id']}")
    assert shown["enabled"] is False
    assert [(v["n"], v["actor"], v["status"]) for v in get(api, f"automations/{proposed['id']}/versions")["versions"]] == [
        (1, "bot:finance", "pending")]
    assert audit_rows(api, proposed["id"]) == []                          # no audit row until a person turns it on
    for tool in ("hub_automation_run", "hub_automation_delete"):
        err, refused = mcp(api, tool, {"id": proposed["id"]}, attempt["token"])
        assert err, (tool, refused)
    err, edit = mcp(api, "hub_automation_update", {"id": anas, "steps": [NOTE_STEP], "summary": "Tell ops"},
                    attempt["token"])
    assert not err and edit == {"id": anas, "pending": 2, "can_edit": False}, edit
    assert get(api, f"automations/{anas}")["version"] == 1                # the live flow waits for a person
    err, refused = mcp(api, "hub_automation_update", {"id": anas, "enabled": False}, attempt["token"])
    assert err, refused                                                     # a switch is not a proposal
    err, refused = mcp(api, "hub_automation_restore", {"id": anas, "n": 2}, attempt["token"])
    assert err, refused
    err, own = mcp(api, "hub_automation_create", {**draft, "name": "Mine", "owner": "bot:finance"}, attempt["token"])
    assert not err and own["can_edit"] is True and own["enabled"] is False, own   # its own: saved, off by default
    on = post(api, f"automations/{proposed['id']}/versions/1/restore", {})    # Ana's Turn on
    assert on["enabled"] is True and on["version"] == 1
    assert get(api, f"automations/{proposed['id']}/versions?status=pending")["versions"] == []
    assert [r["actor"] for r in audit_rows(api, proposed["id"])] == ["human:ana"]


def test_the_guide_carries_the_flow_format(api, botops):
    route = api.get("/api/v2/automations/guide", headers=headers())
    assert route.status_code == 200 and "hub automation create --file -" in route.json()["text"]
    attempt = turn(api, botops, person="cara-test", text="Make me an automation")
    err, guide = mcp(api, "hub_automation_guide", {}, attempt["token"])
    assert not err and "hub automation create --file -" in guide["text"] and "`send_reply`" in guide["text"], guide
    assert "{{flow_shape}}" not in guide["text"]
    assert "`problems`" in guide["text"] and "exact name or id" in guide["text"], guide["text"]  # What wakes
