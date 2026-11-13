"""A real Assistant turn, end to end: the person's own message starts the run, and the Assistant acts with the run's token
and no `--on-behalf-of` header. Each write the person could make on their words goes through directly (no
`confirm_required`); each write that stays the person's own tap is refused. The eval's fake hub answers ok to all of it,
so this file is where a server refusal shows up."""

import json

import pytest

from backend import bot_settings as BS
from backend import hubdb as H
from backend import task_asks
from backend.tests.helpers.api import assign, get, headers, post, runner, setup_attempt  # noqa: F401
from backend.tests.helpers.assistant import assistant_turn, say
from backend.tests.helpers.automation_access import flow


def turn(api, text="Please do it"):
    """The Assistant's turn for the person's own message: its run token, with no citation header."""
    before = getattr(api, "last_turn", None)
    if before is None:
        runner_, attempt = assistant_turn(api, "ana-test", text)
    else:
        # A second turn is the same runner's: the first run ends, then the new message is the next job.
        runner_, first = before
        post(api, f"attempts/{first['id']}/started", {"thread_id": f"turn-{first['id']}"}, token=runner_["token"])
        post(api, f"attempts/{first['id']}/complete", {"outcome": "completed", "last_seq": 0}, token=runner_["token"])
        say(api, f"{text} (turn {first['id'][-6:]})", "ana-test")
        attempt = post(api, "jobs/claim", {}, token=runner_["token"])["attempt"]
    api.last_turn = (runner_, attempt)
    return attempt["token"]


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
    """`tico settings set`: the plan, then the run it names, both with the turn's token."""
    planned = call(api, token, "POST", f"settings/catalog/{key}/plan", {"value": value})
    assert "run" in planned, planned
    run = planned["run"]
    r = api.request(run["method"], "/api/v2/" + run["path"], json=run["body"], headers=headers(token))
    assert r.status_code == 200, (key, r.status_code, r.text)


def repo_bot(api, bot="ops"):
    """A bot with its repository built, as backend/tests/test_bot_tools.py makes one: its config names the repo and its
    computer reports the repository present, so a pause or a resume can place it."""
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE bot_config SET repo=? WHERE bot=?", ("acme-co/emp-" + bot, bot))
        BS.write(c, bot, {"name": bot, "runtime": "codex", "model": "gpt-6-luna", "reasoning_effort": "high"})
    machine = runner(api)
    assign(api, machine, bot)
    row = {
        "ready": True,
        "runtime": "codex",
        "model": "gpt-6-luna",
        "repository_present": True,
        "configuration_valid": True,
        "problems": [],
    }
    post(
        api,
        "runners/heartbeat",
        {
            "version": "test",
            "platform": "test",
            "readiness": {
                "schema_version": 1,
                "runtimes": {"codex": {"installed": True, "authenticated": "ready"}},
                "bots": {bot: row},
            },
        },
        token=machine["token"],
    )


def ana_task(api, owner="bot:ops", title="Send the invoice"):
    with api.app.state.store.transaction() as c:
        return H.task_create(c, owner, title, "Monday.", "human:ana")["id"]


def test_one_assistant_turn_makes_every_write_the_person_could_make_with_no_card(api):
    """Each write the person could make on their words goes through on a turn's token: a close, closes of the tasks listed, an owner
    change, a question answered, settings, automations, a bot paused, resumed and set to a model,
    a rule added and retired, a message and an ask. Every step builds what it acts on, so none leans on another."""
    # A turn closes a task directly.
    task = ana_task(api)
    token = turn(api)
    current = call(api, token, "GET", f"tasks/{task}")["task"]
    closed = call(
        api, token, "POST", f"tasks/{task}/move", {"to": "closed", "why": "Sent.", "version": current["version"]}
    )
    assert closed["task"]["status"] == "closed", ("close", closed)

    # A turn closes the tasks she listed, one at a time (a change to many tasks at once is a person's, on the Tasks
    # page), and only those.
    ids = [ana_task(api, title=f"Follow up {n}") for n in range(2)]
    outside = ana_task(api, title="Not in the list")
    token = turn(api)
    for t in ids:
        version = call(api, token, "GET", f"tasks/{t}")["task"]["version"]
        call(api, token, "POST", f"tasks/{t}/move", {"to": "closed", "why": "Not needed.", "version": version})
    with api.app.state.store.read() as c:
        assert [H.task(c, t)["status"] for t in ids] == ["closed", "closed"], "close"
        assert H.task(c, outside)["status"] == "open", "a close leaves a task outside the list"

    # A turn changes a task owner.
    task = ana_task(api, owner="human:ana")
    token = turn(api)
    current = call(api, token, "GET", f"tasks/{task}")["task"]
    call(api, token, "POST", f"tasks/{task}/owner", {"version": current["version"], "owner": "bot:ops"})
    with api.app.state.store.read() as c:
        assert H.task(c, task)["owner_id"] == "bot:ops", "owner"

    # A turn answers a question the bot asked.
    with api.app.state.store.transaction() as c:
        tool = H.task_create(c, "human:ana", "Pick the outbound tool", "x", "bot:finance", lint=False)
        asked = H.task_ask(c, "bot:finance", tool["id"], "human:ana", "Close or Apollo?", seen_version=tool["version"])
    call(
        api,
        turn(api, "Apollo"),
        "POST",
        f"tasks/{tool['id']}/asks/{asked['requirement_id']}/answer",
        {"value": "Apollo"},
    )

    # A turn sets a direct setting and a confirm setting.
    token = turn(api, "Turn off topic upkeep and the Apollo rule")
    set_setting(api, token, "team.rules.topic_fresh", False)
    set_setting(api, token, "team.rules.botops_direct", False)

    # A turn updates an automation to disabled and to a schedule, then restores an earlier version of another.
    aid = post(api, "automations", flow(owner="person:ana"))["id"]
    token = turn(api, "Turn the digest off, then run it at nine")
    call(api, token, "PATCH", f"automations/{aid}", {"enabled": False})
    call(api, token, "PATCH", f"automations/{aid}", {"trigger": {"type": "schedule", "cron": "0 9 * * *", "tz": "UTC"}})
    aid = post(api, "automations", flow(owner="person:ana"))["id"]
    call(api, "ana-test", "PATCH", f"automations/{aid}", {"name": "Renamed"})  # a second version to restore over
    call(api, turn(api, "Put the first version back"), "POST", f"automations/{aid}/versions/1/restore", {})

    # A turn pauses and resumes a bot, and sets its model.
    repo_bot(api)
    token = turn(api, "Pause Ops until I say")
    rev = call(api, token, "GET", "bots/ops/access")["revision"]
    call(api, token, "POST", "bots/ops/control", {"action": "pause", "expected_revision": rev})
    assert call(api, token, "GET", "bots/ops")["state"] == "paused", "pause"
    rev = call(api, token, "GET", "bots/ops/access")["revision"]
    call(api, token, "POST", "bots/ops/control", {"action": "resume", "expected_revision": rev})
    assert call(api, token, "GET", "bots/ops")["state"] == "active", "resume"
    catalog = get(api, "models")
    model = next(m["id"] for m in catalog["models"] if m.get("runtime") == "codex" and not m.get("deprecated"))
    token = turn(api, "Use the cheaper model for Ops")
    revision = call(api, token, "GET", "bots/ops")["revision"]
    call(api, token, "POST", "bots/ops/model", {"model": model, "expected_revision": revision})

    # A turn adds and retires a rule.
    token = turn(api, "Add the consent rule")
    added = no_card(
        api, token, "POST", "rules", {"kind": "external", "text": "Keep a consent record.", "who": {"bots": ["ops"]}}
    )
    if added.status_code == 200 and added.json().get("id"):
        rule = added.json()
        call(api, turn(api, "Retire it"), "POST", f"rules/{rule['id']}/retire", {"version": rule.get("version", 1)})

    # A turn messages a bot and asks it without waiting.
    token = turn(api, "Tell Ops the invoice is sent")
    call(api, token, "POST", "messages", {"to": "ops", "text": "The invoice is sent.", "kind": "say"})
    call(
        api,
        token,
        "POST",
        "messages",
        {"to": "ops", "text": "Is it paid?", "kind": "say", "needs_action": True, "wait_s": 0},
    )


def test_a_turn_sends_back_an_ok_a_bot_asked_of_her(api):
    """A send back is hers to say through the Assistant; an OK never is."""
    setup_attempt(api, "finance")
    with api.app.state.store.transaction() as c:
        okd = task_asks.ask_ok(
            c,
            "bot:finance",
            "human:ana",
            "Raise the ads budget?",
            shows=[],
            subject="budget_raise:ads",
            content={"amount": 1200},
        )
    token = turn(api, "No, do not spend it")
    answer = f"tasks/{okd['task_id']}/asks/{okd['requirement_id']}/answer"
    # Her Assistant reads the task filed for her OK, as she does: by id and in her task list.
    assert call(api, token, "GET", f"tasks/{okd['task_id']}")["task"]["id"] == okd["task_id"]
    # It never gives the OK, whatever she says.
    refused = call(api, token, "POST", answer, {"value": "ok"}, expected=403)
    assert refused["error"]["code"] == "ok_not_yours", refused
    with api.app.state.store.read() as c:
        assert task_asks.standing(c, okd["requirement_id"]) == task_asks.WAITING
    call(api, token, "POST", answer, {"value": "no", "note": "Not this month."})
    with api.app.state.store.read() as c:
        assert task_asks.standing(c, okd["requirement_id"]) == task_asks.SENT_BACK


def test_another_persons_assistant_neither_reads_nor_sends_back_her_ok(api):
    """The task filed for ana's OK is hers: ben's Assistant, on ben's own words, gets nothing of it."""
    setup_attempt(api, "finance")
    with api.app.state.store.transaction() as c:
        okd = task_asks.ask_ok(
            c,
            "bot:finance",
            "human:ana",
            "Raise the ads budget?",
            shows=[],
            subject="budget_raise:ads",
            content={"amount": 1200},
        )
    _, attempt = assistant_turn(api, "ben-test", "Send back ana's budget OK")
    token = attempt["token"]
    hidden = api.get(f"/api/v2/tasks/{okd['task_id']}", headers=headers(token))
    assert hidden.status_code == 404, hidden.text
    assert "ads budget" not in hidden.text
    listed = api.get("/api/v2/tasks?limit=200", headers=headers(token))
    assert okd["task_id"] not in listed.text
    for value in ("no", "ok"):
        r = api.post(
            f"/api/v2/tasks/{okd['task_id']}/asks/{okd['requirement_id']}/answer",
            json={"value": value, "note": "Not this month."},
            headers=headers(token),
        )
        assert r.status_code == 404, r.text
    with api.app.state.store.read() as c:
        assert task_asks.standing(c, okd["requirement_id"]) == task_asks.WAITING


def test_the_person_alone_decides_these_on_the_turn(api):
    token = turn(api, "Do it")
    for method, path, body in [
        ("POST", "me/tokens", {"name": "new"}),
        ("POST", "credentials/x1/grants", {"person": "human:ben"}),
        ("PATCH", "messages/x1", {"body": "yes"}),
        ("POST", "sql", {"query": "select 1"}),
        ("PUT", "providers/openai", {"key": "x"}),
        ("POST", "support/tickets", {"text": "help"}),
    ]:
        refused_as_card(api, token, method, path, body)
