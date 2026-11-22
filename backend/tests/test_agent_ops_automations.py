"""Agent Ops operates automations (backend/automations.py, backend/agent_ops_act.py): asked by a person it has that person's
rights and saves, edits, pauses, runs, restores and deletes as they could; what it makes is off unless the request
said on. Asked by a bot it has only that bot's rights, so where the bot may not change things it proposes: a pending
version a person turns on. Ana owns the company, Cara is a member (the base fixture).
"""

import json

from backend import agent_ops_act
from backend.store import H
from backend.tests.helpers.api import claim, get, headers, post  # noqa: F401  (fixture)
from backend.tests.helpers.automation_api import NOTE_STEP, audit_rows
from backend.tests.helpers.member_bots import agent_ops, finish, turn  # noqa: F401  (fixtures)
from backend.tests.helpers.mcp import call as mcp

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


LABEL = {"id": "tag", "type": "action", "enabled": True, "do": "label", "args": {"name": "Receipts"}}


def flow(**extra):
    return {"name": "Receipts", "owner": "person:cara", "trigger": CARA_INBOX, "steps": [ARCHIVE], **extra}


def test_a_person_asks_and_agent_ops_saves_off_unless_asked_on(api, agent_ops):
    attempt = turn(api, agent_ops, person="cara-test", text="Archive receipts from my inbox")
    err, made = mcp(api, "tico_automation_create", flow(), attempt["token"])
    assert not err, made
    assert made["enabled"] is False and made["can_edit"] is True and made["owner_id"] == "person:cara"
    assert made["created_by_id"] == "human:cara" and made["created_via"] == "botops"
    assert [(v["n"], v["status"]) for v in get(api, f"automations/{made['id']}/versions", "cara-test")["versions"]] == [
        (1, "active")
    ]
    rows = audit_rows(api, made["id"])
    assert [(r["actor"], r["via"]) for r in rows] == [("human:cara", "botops")]
    finish(api, agent_ops, attempt)
    attempt = turn(api, agent_ops, person="cara-test", text="Archive receipts from my inbox and turn it on")
    err, on = mcp(api, "tico_automation_create", flow(enabled=True), attempt["token"])
    assert not err and on["enabled"] is True, on


def test_a_persons_edits_are_applied_as_they_could(api, agent_ops):
    """Update, pause, run and restore go straight through, with no card and no pending version."""
    aid = post(api, "automations", flow(enabled=True), "cara-test")["id"]
    attempt = turn(api, agent_ops, person="cara-test", text="Label receipts first")
    err, edited = mcp(
        api,
        "tico_automation_update",
        {"id": aid, "steps": [LABEL, ARCHIVE], "summary": "Label first"},
        attempt["token"],
    )
    assert not err and edited["version"] == 2 and "pending" not in edited, edited
    assert get(api, f"automations/{aid}/versions?status=pending", "cara-test")["versions"] == []
    err, paused = mcp(api, "tico_automation_update", {"id": aid, "enabled": False}, attempt["token"])
    assert not err and paused["enabled"] is False, paused
    err, ran = mcp(
        api, "tico_automation_run", {"id": aid, "input": {"message": {"subject": "hello"}}}, attempt["token"]
    )
    assert not err, ran
    err, back = mcp(api, "tico_automation_restore", {"id": aid, "n": 1}, attempt["token"])
    assert not err and back["version"] == 3 and len(back["steps"]) == 1, back
    for answer in (edited, paused, ran, back):
        assert "needs_confirm" not in answer  # nothing waited on a card
    for method, path in (
        ("POST", "automations"),
        ("PATCH", f"automations/{aid}"),
        ("POST", f"automations/{aid}/run"),
        ("POST", f"automations/{aid}/versions/1/restore"),
        ("DELETE", f"automations/{aid}/versions/2"),
    ):
        assert agent_ops_act.classify(method, path) == "do" and not agent_ops_act.card_for_person(method, path), path
    assert {r["actor"] for r in audit_rows(api, aid)} == {"human:cara"}


def test_a_persons_delete_waits_for_their_own_tap(api, agent_ops):
    """Deleting an automation is a Confirm card in the person's Agent Ops chat, as deleting a routine is."""
    aid = post(api, "automations", flow(enabled=True), "cara-test")["id"]
    attempt = turn(api, agent_ops, person="cara-test", text="Delete my receipts automation")
    err, card = mcp(api, "tico_automation_delete", {"id": aid}, attempt["token"])
    assert not err and card["needs_confirm"], card
    assert get(api, f"automations/{aid}", "cara-test")["id"] == aid  # nothing changed yet
    for method, path in (("POST", f"automations/{aid}/delete"), ("DELETE", f"automations/{aid}")):
        assert agent_ops_act.classify(method, path) == "confirm" and agent_ops_act.card_for_person(method, path), path
        assert agent_ops_act.card_summary(method, path) == "Delete an automation"
    post(api, f"assistant/actions/{card['action']['id']}/confirm", {}, token=attempt["token"], expected=403)
    assert (
        post(api, f"assistant/actions/{card['action']['id']}/confirm", {}, token="cara-test")["action"]["status"]
        == "done"
    )
    get(api, f"automations/{aid}", "cara-test", expected=404)


def test_agent_ops_never_goes_past_the_askers_rights(api, agent_ops):
    anas = post(api, "automations", flow(owner="person:ana", trigger={"type": "message", "inbox": "ana@acme.example"}))
    attempt = turn(api, agent_ops, person="cara-test", text="Draft replies to everything in Ana's inbox")
    err, refused = mcp(
        api, "tico_automation_create", flow(trigger={"type": "message", "inbox": "ana@acme.example"}), attempt["token"]
    )
    assert err, refused  # Cara may not read Ana's mail
    err, refused = mcp(api, "tico_automation_create", flow(owner="person:ana"), attempt["token"])
    assert err, refused  # nor make Ana one
    err, refused = mcp(api, "tico_automation_update", {"id": anas["id"], "steps": []}, attempt["token"])
    assert err, refused  # nor edit Ana's, or propose to it
    err, refused = mcp(api, "tico_automation_delete", {"id": anas["id"]}, attempt["token"])
    assert err, refused
    assert get(api, f"automations/{anas['id']}/versions")["versions"][0]["status"] == "active"
    assert len(get(api, f"automations/{anas['id']}/versions")["versions"]) == 1
    assert [a["id"] for a in get(api, "automations", "cara-test")["automations"] if not a["builtin"]] == []


def test_a_bot_asks_and_agent_ops_proposes_where_that_bot_may_not_change(api, agent_ops):
    anas = post(api, "automations", flow(owner="person:ana", trigger={"type": "event", "name": "x.y"}))["id"]
    with api.app.state.store.transaction() as c:
        H.task_create(c, "bot:finance", "Automate the receipts", "Archive receipts.", "bot:botops", lint=False)
    attempt = claim(api, agent_ops, "botops")
    draft = {"name": "Ana's receipts", "owner": "person:ana", "trigger": {"type": "event", "name": "x.y"}, "steps": []}
    err, proposed = mcp(api, "tico_automation_create", draft, attempt["token"])
    assert not err and proposed["pending"] == 1 and proposed["can_edit"] is False, proposed
    shown = get(api, f"automations/{proposed['id']}")
    assert shown["enabled"] is False
    assert [
        (v["n"], v["actor"], v["status"]) for v in get(api, f"automations/{proposed['id']}/versions")["versions"]
    ] == [(1, "bot:finance", "pending")]
    assert audit_rows(api, proposed["id"]) == []  # no audit row until a person turns it on
    for tool in ("tico_automation_run", "tico_automation_delete"):
        err, refused = mcp(api, tool, {"id": proposed["id"]}, attempt["token"])
        assert err, (tool, refused)
    err, edit = mcp(
        api, "tico_automation_update", {"id": anas, "steps": [NOTE_STEP], "summary": "Tell ops"}, attempt["token"]
    )
    assert not err and edit == {"id": anas, "pending": 2, "can_edit": False}, edit
    assert get(api, f"automations/{anas}")["version"] == 1  # the live flow waits for a person
    err, refused = mcp(api, "tico_automation_update", {"id": anas, "enabled": False}, attempt["token"])
    assert err, refused  # a switch is not a proposal
    err, refused = mcp(api, "tico_automation_restore", {"id": anas, "n": 2}, attempt["token"])
    assert err, refused
    err, own = mcp(api, "tico_automation_create", {**draft, "name": "Mine", "owner": "bot:finance"}, attempt["token"])
    assert not err and own["can_edit"] is True and own["enabled"] is False, own  # its own: saved, off by default
    on = post(api, f"automations/{proposed['id']}/versions/1/restore", {})  # Ana's Turn on
    assert on["enabled"] is True and on["version"] == 1
    assert on["created_by_id"] == "bot:finance" and on["created_via"] == "botops"
    assert get(api, f"automations/{proposed['id']}/versions?status=pending")["versions"] == []
    assert [r["actor"] for r in audit_rows(api, proposed["id"])] == ["human:ana"]


def test_a_delete_card_names_the_automation_the_work_and_why(api, agent_ops):
    """The card says which automation, whose, that it is already off, the work Agent Ops was on and why it waits for
    this person: from the database, never only "Make a change in Tico (automations)"."""
    aid = post(api, "automations", flow(enabled=False), "cara-test")["id"]
    attempt = turn(api, agent_ops, person="cara-test", text="Delete my receipts automation")
    err, card = mcp(api, "tico_automation_delete", {"id": aid}, attempt["token"])
    assert not err and card["needs_confirm"], card
    title = card["action"]["description"]
    assert title.startswith("Delete the automation “Receipts” of Cara (already switched off). "), title
    assert "Agent Ops asked in your chat with it." in title and "always needs your own OK" in title, title
    assert "Make a change in Tico" not in title


def test_a_card_on_a_task_names_the_task_and_who_asked(api, agent_ops):
    """Agent Ops working on a task a person asked for names that task and its requester; a task the person may not
    read is named only by who asked for it."""
    aid = post(api, "automations", flow(enabled=True), "cara-test")["id"]
    with api.app.state.store.transaction() as c:
        task = H.task_create(c, "human:cara", "Tidy my automations", "Delete the old ones.", "bot:botops", lint=False)
    attempt = claim(api, agent_ops, "botops")
    err, card = mcp(api, "tico_automation_delete", {"id": aid}, attempt["token"])
    assert not err and card["needs_confirm"], card
    title = card["action"]["description"]
    assert "(already switched off)" not in title, title
    assert "Agent Ops asked while working on “Tidy my automations”, which you asked for." in title, (title, task)


def test_a_card_never_shows_a_task_the_person_may_not_read_or_fails_on_a_gone_automation(api, agent_ops):
    """A task the person may not read is named only by who asked for it, and an automation that is gone is named by
    its id, so the card is still made."""
    from backend import assistant

    with api.app.state.store.transaction() as c:
        task = H.task_create(c, "human:ana", "Ana's private plan", "", "bot:botops", lint=False, private=True)
        c.execute(
            "INSERT INTO attempts(id, bot_id, task_id, state, created_at) VALUES('a-x', 'botops', ?, 'running', ?)",
            (task["id"], H.now()),
        )
        cara = api.app.state.auth.identity_for_actor(c, "human:cara")
        why = agent_ops_act.card_why(c, api.app.state.auth, cara, "a-x", "DELETE", "/api/v2/automations/gone")
        said = assistant.describe(c, "DELETE", "/api/v2/automations/gone", {})[0]
    assert why.startswith("Agent Ops asked while working for Ana."), why
    assert "Ana's private plan" not in why
    assert said == "Delete the automation gone", said


def test_a_delete_of_a_gone_automation_is_refused_with_no_card(api, agent_ops):
    """Spec AC-4 as corrected: deleting an automation already gone is refused at once, through the same path that
    makes cards, and leaves no card behind for the person to confirm."""
    attempt = turn(api, agent_ops, person="cara-test", text="Delete my receipts automation")
    with api.app.state.store.transaction() as c:
        before = c.execute("SELECT count(*) FROM assistant_actions").fetchone()[0]
    err, said = mcp(api, "tico_automation_delete", {"id": "gone"}, attempt["token"])
    assert err and "No such automation" in json.dumps(said), said
    with api.app.state.store.transaction() as c:
        assert c.execute("SELECT count(*) FROM assistant_actions").fetchone()[0] == before


def test_a_card_says_why_by_its_rule_and_never_calls_a_gone_task_a_chat(api, agent_ops):
    """Two protected changes give their own reasons; a run whose task is gone names that task's id instead of
    claiming the person asked in the chat."""
    with api.app.state.store.transaction() as c:
        c.execute(
            "INSERT INTO attempts(id, bot_id, task_id, state, created_at) VALUES('a-gone', 'botops', 'gone-task', "
            "'running', ?)",
            (H.now(),),
        )
        cara = api.app.state.auth.identity_for_actor(c, "human:cara")
        why = lambda method, path: agent_ops_act.card_why(c, api.app.state.auth, cara, "a-gone", method, path)
        repo = why("PUT", "/api/v2/bots/ops/repositories")
        key = why("POST", "/api/v2/credentials/k1/grants")
        other = why("POST", "/api/v2/somewhere/new")
    assert repo.startswith("Agent Ops asked while working on task gone-task, which is no longer there. "), repo
    assert "repositories" in repo and "stored credential" in key and repo != key != other
    assert "chat" not in repo
