"""A real Assistant turn, end to end: the person's own message starts the run, and the Assistant acts with the run's token
and no `--on-behalf-of` header. Each write the person could make on their words goes through directly (no
`confirm_required`); each write that stays the person's own tap is refused. The eval's fake hub answers ok to all of it,
so this file is where a server refusal shows up."""

import json

import pytest

from backend import hubdb as H
from backend.tests.test_api import api, assign, get, headers, post, runner, setup_attempt  # noqa: F401
from backend.tests.test_assistant import assistant_turn, say
from backend.tests.test_automation_access import flow
from backend.tests.test_batch import approval_for_ana


def turn(api, text="Please do it"):
    """The Assistant's turn for the person's own message: its run token, with no citation header."""
    return assistant_turn(api, "ana-test", text)[1]["token"]


def call(api, token, method, path, body=None, expected=200):
    r = api.request(method, "/api/v2/" + path, json=body, headers=headers(token))
    assert r.status_code == expected, (method, path, r.status_code, r.text)
    return r.json() if r.content else {}


def no_card(api, token, method, path, body=None):
    """The write is admitted on the turn: whatever the route answers, the gate did not put it on a card."""
    r = api.request(method, "/api/v2/" + path, json=body, headers=headers(token))
    assert not (r.status_code == 403 and r.json().get("error", {}).get("code") == "confirm_required"), (path, r.text)
    return r


def refused_as_card(api, token, method, path, body=None):
    r = api.request(method, "/api/v2/" + path, json=body, headers=headers(token))
    assert r.status_code == 403 and r.json()["error"]["code"] == "confirm_required", (path, r.status_code, r.text)


def set_setting(api, token, key, value):
    """`hub settings set`: the plan, then the run it names, both with the turn's token."""
    planned = call(api, token, "POST", f"settings/catalog/{key}/plan", {"value": value})
    assert "run" in planned, planned
    run = planned["run"]
    r = api.request(run["method"], "/api/v2/" + run["path"], json=run["body"], headers=headers(token))
    assert r.status_code == 200, (key, r.status_code, r.text)


def repo_bot(api, bot="ops"):
    """A bot with its repository built, as backend/tests/test_bot_tools.py makes one: its config names the repo and its
    computer reports the repository present, so a pause or a resume can place it."""
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE bot_config SET config=?, repo=? WHERE bot=?",
                  (json.dumps({"name": bot, "runtime": "codex", "model": "gpt-6-luna", "reasoning_effort": "high"}),
                   "acme-co/emp-" + bot, bot))
    machine = runner(api)
    assign(api, machine, bot)
    row = {"ready": True, "runtime": "codex", "model": "gpt-6-luna", "repository_present": True,
           "configuration_valid": True, "problems": []}
    post(api, "runners/heartbeat", {"version": "test", "platform": "test",
                                    "readiness": {"schema_version": 1, "runtimes": {"codex": {"installed": True, "authenticated": "ready"}},
                                                  "bots": {bot: row}}}, token=machine["token"])


def ana_task(api, owner="bot:ops", title="Send the invoice"):
    with api.app.state.store.transaction() as c:
        return H.task_create(c, owner, title, "Monday.", "human:ana")["id"]


def test_a_turn_closes_a_task_directly(api):
    task = ana_task(api)
    token = turn(api)
    current = call(api, token, "GET", f"tasks/{task}")["task"]
    closed = call(api, token, "POST", f"tasks/{task}", {"version": current["version"], "status": "done"})
    assert (closed.get("task") or closed)["status"] == "done", closed


def test_a_turn_closes_tasks_in_bulk(api):
    ids = [ana_task(api, title=f"Follow up {n}") for n in range(2)]
    outside = ana_task(api, title="Not in the list")
    token = turn(api)
    done = call(api, token, "POST", "task-bulk", {"filter": {"ids": ids}, "action": "close"})
    assert done["changed"] == 2, done
    with api.app.state.store.read() as c:
        assert [H.task(c, t)["status"] for t in ids] == ["closed", "closed"]
        assert H.task(c, outside)["status"] == "open"


def test_a_turn_changes_a_task_owner(api):
    task = ana_task(api)
    token = turn(api)
    current = call(api, token, "GET", f"tasks/{task}")["task"]
    call(api, token, "POST", f"tasks/{task}", {"version": current["version"], "owner": "bot:ops"})


def test_a_turn_declines_an_approval(api):
    approval = approval_for_ana(api)["id"]
    token = turn(api, "No, do not spend it")
    call(api, token, "POST", f"approvals/{approval}", {"decision": "declined", "note": ""})


def test_a_turn_approves_after_a_read_back_and_a_plain_yes_with_the_setting_on(api):
    approval = approval_for_ana(api)["id"]
    post(api, "assistant/settings", {"decides_approvals": True}, token="ana-test")
    runner, first = assistant_turn(api, "ana-test", "Go ahead with the ads")
    call(api, first["token"], "POST", "assistant/read-back", {"approvals": [approval]})
    # The first run ends, then the next turn is the same runner's: its yes is a new message and the next job.
    post(api, f"attempts/{first['id']}/started", {"thread_id": "first-run"}, token=runner["token"])
    post(api, f"attempts/{first['id']}/complete", {"outcome": "completed", "last_seq": 0}, token=runner["token"])
    say(api, "Yes", "ana-test")
    yes = post(api, "jobs/claim", {}, token=runner["token"])["attempt"]["token"]
    call(api, yes, "POST", f"approvals/{approval}", {"decision": "approved", "note": ""})


def test_a_turn_answers_a_question_the_bot_asked(api):
    finance = setup_attempt(api, "finance")[2]["token"]
    asked = call(api, finance, "POST", "messages", {"to": "human:ana", "text": "Close or Apollo?", "kind": "ask"})
    call(api, turn(api, "Apollo"), "POST", f"messages/{asked['id']}/answer", {"text": "Apollo"})


def test_a_turn_sets_a_direct_setting_and_a_confirm_setting(api):
    token = turn(api, "Turn off the stale review and the Apollo rule")
    set_setting(api, token, "team.rules.stale_review", False)
    set_setting(api, token, "team.rules.botops_direct", False)


def test_a_turn_updates_an_automation_to_disabled_and_to_a_schedule(api):
    aid = post(api, "automations", flow(owner="person:ana"))["id"]
    token = turn(api, "Turn the digest off, then run it at nine")
    call(api, token, "PATCH", f"automations/{aid}", {"enabled": False})
    call(api, token, "PATCH", f"automations/{aid}", {"trigger": {"type": "schedule", "cron": "0 9 * * *", "tz": "UTC"}})


def test_a_turn_restores_an_earlier_automation_version(api):
    aid = post(api, "automations", flow(owner="person:ana"))["id"]
    call(api, "ana-test", "PATCH", f"automations/{aid}", {"name": "Renamed"})   # a second version to restore over
    token = turn(api, "Put the first version back")
    call(api, token, "POST", f"automations/{aid}/versions/1/restore", {})


def test_a_turn_pauses_and_resumes_a_bot(api):
    repo_bot(api)
    token = turn(api, "Pause Ops until I say")
    rev = call(api, token, "GET", "bots/ops/access")["revision"]
    call(api, token, "POST", "bots/ops/control", {"action": "pause", "expected_revision": rev})
    assert call(api, token, "GET", "bots/ops")["state"] == "paused"
    rev = call(api, token, "GET", "bots/ops/access")["revision"]
    call(api, token, "POST", "bots/ops/control", {"action": "resume", "expected_revision": rev})
    assert call(api, token, "GET", "bots/ops")["state"] == "active"


def test_a_turn_sets_a_bot_model(api):
    repo_bot(api)
    catalog = get(api, "models")
    model = next(m["id"] for m in catalog["models"] if m.get("runtime") == "codex" and not m.get("deprecated"))
    token = turn(api, "Use the cheaper model for Ops")
    revision = call(api, token, "GET", "bots/ops")["revision"]
    call(api, token, "POST", "bots/ops/model", {"model": model, "expected_revision": revision})


def test_a_turn_adds_and_retires_a_rule(api):
    token = turn(api, "Add the consent rule")
    added = no_card(api, token, "POST", "rules", {"kind": "external", "text": "Keep a consent record.",
                                                    "who": {"bots": ["ops"]}})
    if added.status_code == 200 and added.json().get("id"):
        rule = added.json()
        call(api, turn(api, "Retire it"), "POST", f"rules/{rule['id']}/retire", {"version": rule.get("version", 1)})


def test_a_turn_messages_a_bot_and_asks_it_without_waiting(api):
    token = turn(api, "Tell Ops the invoice is sent")
    call(api, token, "POST", "messages", {"to": "ops", "text": "The invoice is sent.", "kind": "say"})
    call(api, token, "POST", "messages", {"to": "ops", "text": "Is it paid?", "kind": "ask", "wait_s": 0})


@pytest.mark.parametrize("method,path,body", [
    ("POST", "me/tokens", {"name": "new"}),
    ("POST", "credentials/x1/grants", {"person": "human:ben"}),
    ("PATCH", "messages/x1", {"body": "yes"}),
    ("POST", "sql", {"query": "select 1"}),
    ("PUT", "providers/openai", {"key": "x"}),
    ("POST", "support/tickets", {"text": "help"}),
])
def test_the_person_alone_decides_these_on_the_turn(api, method, path, body):
    refused_as_card(api, turn(api, "Do it"), method, path, body)


def test_the_background_run_puts_a_card_in_the_persons_chat(api):
    coo = setup_attempt(api, "coo")[2]["token"]
    made = post(api, "assistant/actions", {"summary": "Archive the Ops bot", "method": "POST",
                                           "path": "/api/v2/bots/ops/archive", "body": {"expected_revision": 1},
                                           "for": "human:ana"}, token=coo)["action"]
    assert made["owner"] == "human:ana" and made["status"] == "pending"
