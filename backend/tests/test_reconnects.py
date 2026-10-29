"""A bot's task stops on an account a person must fix: one card in its thread, a wait, one wake when it works again.

Before: a refused sign-in or a missing secret on a task left the task open, so stall checks and retries kept waking its
bot, and the person who could fix it got a standalone hold item that never named the task.
"""

import json

from backend import hubdb as H
from backend import reconnects as R
from backend.tests.test_api import api, assign, claim, get, headers, post, runner  # noqa: F401


def heartbeat(api, machine, signed_in):
    post(api, "runners/heartbeat", {"version": "test", "platform": "test", "readiness": {
        "schema_version": 1, "runtimes": {"codex": {"installed": True, "authenticated": "ready" if signed_in else "missing"}},
        "bots": {"ops": {"runtime": "codex", "ready": True}}}}, machine["token"])


def codex_bot(api):
    from backend.tests.test_subscriptions import set_runtime
    machine = runner(api)
    assign(api, machine, "ops")
    set_runtime(api, "codex")
    heartbeat(api, machine, True)
    return machine


def task(api, title="Send the Acme renewal quote"):
    return post(api, "tasks", {"owner": "ops", "title": title, "body": "Draft and send it."})


def shown(api, t, token="ana-test"):
    return get(api, "tasks/" + t["id"], token)


def wakes(api, t):
    """The reconnect wakes the task's owner got."""
    with api.app.state.store.read() as c:
        return [r["body"] for r in c.execute(
            "SELECT m.body FROM messages m JOIN conversations cv ON cv.id=m.conversation_id "
            f"WHERE {H.MESSAGE_TASK_SQL}=? AND m.to_actor='bot:ops' "
            "AND json_extract(m.refs_json,'$.reconnected') IS NOT NULL", (t["id"],))]


def cards(api, t):
    with api.app.state.store.read() as c:
        return [json.loads(r[0])["reconnect"] for r in c.execute(
            "SELECT m.refs_json FROM messages m JOIN conversations cv ON cv.id=m.conversation_id "
            f"WHERE {H.MESSAGE_TASK_SQL}=? AND json_extract(m.refs_json,'$.reconnect') IS NOT NULL", (t["id"],))]


def needs(api, token="ana-test"):
    return get(api, "needs-you", token)["items"]


def open_card(api, t, key="google:ana@acme.example", person="human:ana"):
    with api.app.state.store.transaction() as c:
        return R.open(c, t["id"], "ops", key, "google", f"Google ({key.split(':', 1)[1]})", person,
                      {"kind": "google", "account": key.split(":", 1)[1]})


def test_a_refused_sign_in_cards_the_task_which_waits_and_wakes_once_when_signed_in(api):
    machine = codex_bot(api)
    t = task(api)
    attempt = claim(api, machine)
    post(api, f"attempts/{attempt['id']}/started", {"thread_id": "reconnect"}, machine["token"])
    post(api, f"attempts/{attempt['id']}/complete", {
        "outcome": "failed", "last_seq": 0,
        "auth_rejected": {"runtime": "codex", "reason": "OAuth session expired and could not be refreshed"}},
        machine["token"])
    [card] = cards(api, t)
    assert card["key"] == f"login:{machine['runner_id']}:codex" and card["status"] == "open"
    assert card["person"] == "human:ana" and card["fix"]["runner"] == machine["runner_id"]
    assert shown(api, t)["task"]["status"] == "waiting"
    with api.app.state.store.read() as c:
        assert H.waiting_for(c, H.task(c, t["id"])) == "Codex on Test Mac to be reconnected"
    [item] = [i for i in needs(api) if i["id"] == t["id"]]
    assert item["need"] == "connect" and item["reconnect"]["key"] == card["key"]
    # The same refusal again (the queued job's retry) changes nothing: one card, one item.
    with api.app.state.store.transaction() as c:
        R.login_refused(c, api.app.state.store.settings, "ops", machine["runner_id"], "codex", t["id"])
    assert len(cards(api, t)) == 1
    heartbeat(api, machine, True)
    assert [c["status"] for c in cards(api, t)] == ["fixed"]
    assert shown(api, t)["task"]["status"] in ("open", "doing")         # back to the working step it held
    assert wakes(api, t) == [f"Reconnected: {t['title']}\nCodex on Test Mac is reconnected. Continue."]
    # Another healthy report finds nothing open: still one wake.
    post(api, "runners/heartbeat", {"version": "test2", "platform": "test", "readiness": {
        "schema_version": 1, "runtimes": {"codex": {"installed": True, "authenticated": "ready"}},
        "bots": {"ops": {"runtime": "codex", "ready": True}}}}, machine["token"])
    assert len(wakes(api, t)) == 1
    assert not [i for i in needs(api) if i["id"] == t["id"]]


def test_two_tasks_on_one_account_are_one_needs_you_item_and_each_wakes_once(api):
    first, second = task(api, "Reply to Acme"), task(api, "Book the Acme call")
    open_card(api, first)
    open_card(api, second)
    items = [i for i in needs(api) if i.get("reconnect")]
    assert len(items) == 1 and items[0]["id"] == first["id"]
    assert [f["id"] for f in items[0]["folded"]] == [second["id"]]
    with api.app.state.store.transaction() as c:
        assert R.google_works(c, []) == [first["id"], second["id"]]
        assert R.google_works(c, []) == []
    assert len(wakes(api, first)) == len(wakes(api, second)) == 1
    assert {shown(api, t)["task"]["status"] for t in (first, second)} == {"open"}


def test_a_task_waiting_on_its_card_gets_no_stall_wake_and_is_not_stranded(api):
    t = task(api)
    open_card(api, t)
    later = H.shift(H.now(), hours=30)
    with api.app.state.store.transaction() as c:
        assert t["id"] not in {r["id"] for r in H.stalled_tasks(c, later)}
        H.sweep_stranded(c, later)
        assert H.task(c, t["id"])["status"] == "waiting"
        from backend import wake_rules
        assert wake_rules.parked(c, H.task(c, t["id"])) == "reconnect"


def test_only_the_person_who_can_fix_it_has_the_item(api):
    t = task(api)
    open_card(api, t, key="google:cara@acme.example", person="human:cara")
    # Cara's own mailbox: hers to connect, not the owner's or an admin's.
    assert [i["id"] for i in needs(api, "cara-test") if i.get("reconnect")] == [t["id"]]
    assert not [i for i in needs(api, "ana-test") if i.get("reconnect")]
    assert not [i for i in needs(api, "ben-test") if i.get("reconnect")]


def test_the_connector_cards_the_mailbox_bots_recent_task_and_a_clean_report_fixes_it(api, monkeypatch):
    from backend import routines
    t = task(api)
    other = post(api, "tasks", {"owner": "cpo", "title": "Plan Q4", "body": "x"})
    monkeypatch.setattr(routines, "message_bot_mailboxes",
                        lambda c, roster=None: [{"address": "ana@acme.example", "person_id": "ana", "bot": "ops", "declared": False}])
    with api.app.state.store.transaction() as c:
        H.turn_start(c, H.KEEPER, "ops", task_id=t["id"])
        c.execute("INSERT INTO service_health(service,last_success,last_error,detail_json) VALUES('connector:calendar',NULL,"
                  "'Calendar can''t refresh',?)", (json.dumps({"signin": ["ana@acme.example"]}),))
        assert R.google_report(c, ["ana@acme.example"]) == [t["id"]]
        # Mail reports clean while the calendar still fails for the mailbox: still open.
        R.google_report(c, [])
        assert R.pending(c, t["id"])
        c.execute("UPDATE service_health SET last_error=NULL WHERE service='connector:calendar'")
        R.google_report(c, [])
        assert not R.pending(c, t["id"])
    assert cards(api, other) == []
    assert len(wakes(api, t)) == 1


def test_a_bot_on_a_task_opens_a_credential_card_in_its_thread_and_saving_it_wakes_the_task_once(api):
    machine = runner(api)
    assign(api, machine, "ops")
    post(api, "runners/heartbeat", {"version": "test", "platform": "test", "readiness": {"ops": True}}, machine["token"])
    t = task(api)
    attempt = claim(api, machine)
    opened = api.post("/api/v2/credential-requests", json={"env": "ACME_CRM_TOKEN", "label": "the Acme CRM token"},
                      headers=headers(attempt["token"]))
    assert opened.status_code == 200, opened.text
    [card] = cards(api, t)
    assert card["key"] == "secret:ACME_CRM_TOKEN" and card["person"] == "human:ana"
    assert shown(api, t)["task"]["status"] == "waiting"
    post(api, f"credential-requests/{opened.json()['id']}/save", {"value": "crm-token-0123456789"})
    assert [c["status"] for c in cards(api, t)] == ["fixed"]
    assert wakes(api, t) == [f"Reconnected: {t['title']}\nthe Acme CRM token is saved. Continue."]
    with api.app.state.store.read() as c:
        # The wake is the bot's one notice: no second "Saved ..." message from the person.
        assert not c.execute("SELECT 1 FROM messages WHERE json_extract(refs_json,'$.credential_saved') IS NOT NULL").fetchone()
