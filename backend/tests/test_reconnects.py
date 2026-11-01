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
            "AND json_extract(m.refs,'$.reconnected') IS NOT NULL", (t["id"],))]


def cards(api, t):
    with api.app.state.store.read() as c:
        return [json.loads(r[0])["reconnect"] for r in c.execute(
            "SELECT m.refs FROM messages m JOIN conversations cv ON cv.id=m.conversation_id "
            f"WHERE {H.MESSAGE_TASK_SQL}=? AND json_extract(m.refs,'$.reconnect') IS NOT NULL", (t["id"],))]


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
    assert card["key"] == f"login:{machine['runner_id']}:codex:_default" and card["status"] == "open"
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


def refused(api, machine, attempt, profile=None):
    post(api, f"attempts/{attempt['id']}/started", {"thread_id": "reconnect"}, machine["token"])
    post(api, f"attempts/{attempt['id']}/complete", {
        "outcome": "failed", "last_seq": 0, **({"profile_used": profile} if profile else {}),
        "auth_rejected": {"runtime": "codex", "reason": "OAuth session expired and could not be refreshed"}},
        machine["token"])


def next_turn(api, machine):
    """The bot's next claim, past the short retry wait its failed run started (backend/run_backoff.py)."""
    with api.app.state.store.transaction() as c:
        c.execute("DELETE FROM run_backoff WHERE bot='ops'")
    return claim(api, machine)


def report(api, machine, codex):
    post(api, "runners/heartbeat", {"version": "test", "platform": "test", "readiness": {
        "schema_version": 1, "runtimes": {"codex": {"installed": True, **codex}},
        "bots": {"ops": {"runtime": "codex", "ready": True}}}}, machine["token"])


def test_a_refused_profile_waits_for_that_profile_not_the_computers_own_login(api):
    machine = codex_bot(api)
    t = task(api)
    refused(api, machine, claim(api, machine), profile="work")
    [card] = cards(api, t)
    assert card["key"] == f"login:{machine['runner_id']}:codex:work" and card["label"] == "Codex on Test Mac (work)"
    # The computer's own login ready, a good run on it, and that login ready beside the still-refused profile: the
    # card stands through all three.
    report(api, machine, {"authenticated": "ready", "detail": "signed in"})
    task(api, "Book the Acme call")
    done = next_turn(api, machine)
    post(api, f"attempts/{done['id']}/started", {"thread_id": "other"}, machine["token"])
    post(api, f"attempts/{done['id']}/complete", {"outcome": "completed", "last_seq": 0}, machine["token"])
    report(api, machine, {"authenticated": "failed", "profiles": {
        "": {"authenticated": "ready"}, "work": {"authenticated": "failed"}}})
    assert [c["status"] for c in cards(api, t)] == ["open"] and wakes(api, t) == []
    # That profile's own row ready: fixed, one wake.
    report(api, machine, {"authenticated": "ready", "profiles": {
        "": {"authenticated": "ready"}, "work": {"authenticated": "ready"}}})
    assert [c["status"] for c in cards(api, t)] == ["fixed"] and len(wakes(api, t)) == 1


def test_a_run_that_completes_on_the_refused_profile_fixes_its_card_and_a_fallback_run_does_not(api):
    machine = codex_bot(api)
    t = task(api)
    refused(api, machine, claim(api, machine), profile="work")
    for extra, status in (({"profile_used": "work", "fallback": "claude"}, "open"), ({"profile_used": "work"}, "fixed")):
        task(api, f"Reply to Acme ({status})")
        done = next_turn(api, machine)
        post(api, f"attempts/{done['id']}/started", {"thread_id": status}, machine["token"])
        post(api, f"attempts/{done['id']}/complete", {"outcome": "completed", "last_seq": 0, **extra}, machine["token"])
        assert [c["status"] for c in cards(api, t)] == [status]
    assert len(wakes(api, t)) == 1


def test_which_report_row_is_a_logins_own():
    own = {"authenticated": "ready"}
    profiles = {"runtimes": {"codex": {"authenticated": "rejected", "profiles": {
        "": own, "work": {"authenticated": "rejected"}, "lab": {"shared_login": True, "authenticated": "missing"}}}}}
    assert R._login_state(profiles, "codex", "") == own
    assert R._login_state(profiles, "codex", "work") == {"authenticated": "rejected"}
    assert R._login_state(profiles, "codex", "lab") == own                        # shares the computer's login
    assert R._login_state(profiles, "codex", "gone") == {}                         # unreported: not ready
    single = {"computer_profile": "acme", "runtimes": {"codex": own}}
    assert R._login_state(single, "codex", "") == R._login_state(single, "codex", "acme") == own
    assert R._login_state(single, "codex", "work") == {}
    assert R._login_state({}, "codex", "") == {}


def test_two_tasks_on_one_account_are_one_needs_you_item_and_each_wakes_once(api):
    first, second = task(api, "Reply to Acme"), task(api, "Book the Acme call")
    open_card(api, first)
    open_card(api, second)
    items = [i for i in needs(api) if i.get("reconnect")]
    assert len(items) == 1 and items[0]["id"] == first["id"]
    assert [f["id"] for f in items[0]["folded"]] == [second["id"]]
    with api.app.state.store.transaction() as c:
        assert R.google_connected(c, "ana@acme.example") == [first["id"], second["id"]]
        assert R.google_connected(c, "ana@acme.example") == []
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


def test_the_connector_cards_the_mailbox_bots_recent_task_and_only_every_refusing_service_fixes_it(api, monkeypatch):
    from backend import routines
    t = task(api)
    other = post(api, "tasks", {"owner": "cpo", "title": "Plan Q4", "body": "x"})
    monkeypatch.setattr(routines, "message_bot_mailboxes",
                        lambda c, roster=None: [{"address": "ana@acme.example", "person_id": "ana", "bot": "ops", "declared": False}])
    box = "Ana@acme.example"
    with api.app.state.store.transaction() as c:
        H.turn_start(c, H.KEEPER, "ops", task_id=t["id"])
        assert R.google_report(c, "mail", [box]) == [t["id"]]
        R.google_report(c, "calendar", [box])
        # Mail works again (a clean report, a synced mailbox) while the calendar has not said so: still open.
        R.google_report(c, "mail", [])
        R.google_good(c, "mail", [box])
        assert R.pending(c, t["id"])
        # The calendar failing for another reason is no news about its sign-in.
        R.google_report(c, "calendar", [], [box])
        assert R.pending(c, t["id"])
        R.google_good(c, "calendar", [box])
        assert not R.pending(c, t["id"])
    assert cards(api, other) == []
    assert len(wakes(api, t)) == 1


def test_a_service_that_never_refused_does_not_hold_the_card_and_one_that_did_must_report(api, monkeypatch):
    from backend import routines
    first, second = task(api, "Reply to Acme"), task(api, "Book the Acme call")
    monkeypatch.setattr(routines, "message_bot_mailboxes",
                        lambda c, roster=None: [{"address": "ana@acme.example", "person_id": "ana", "bot": "ops", "declared": False}])
    with api.app.state.store.transaction() as c:
        H.turn_start(c, H.KEEPER, "ops", task_id=first["id"])
        R.google_report(c, "mail", ["ana@acme.example"])
        # No calendar report at all: mail working again is enough.
        assert R.google_good(c, "mail", ["ana@acme.example"]) == [first["id"]]
        H.turn_start(c, H.KEEPER, "ops", task_id=second["id"])
        R.google_report(c, "mail", ["ana@acme.example"])
        R.google_report(c, "calendar", ["ana@acme.example"])
        # A clean mail report while the calendar stays silent: the calendar refused, so it must say so first.
        assert R.google_report(c, "mail", []) == []
        assert R.pending(c, second["id"])


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
    assert card["key"] == "secret:ops:ACME_CRM_TOKEN" and card["person"] == "human:ana"
    assert shown(api, t)["task"]["status"] == "waiting"
    post(api, f"credential-requests/{opened.json()['id']}/save", {"value": "crm-token-0123456789"})
    assert [c["status"] for c in cards(api, t)] == ["fixed"]
    assert wakes(api, t) == [f"Reconnected: {t['title']}\nthe Acme CRM token is saved. Continue."]
    with api.app.state.store.read() as c:
        # The wake is the bot's one notice: no second "Saved ..." message from the person.
        assert not c.execute("SELECT 1 FROM messages WHERE json_extract(refs,'$.credential_saved') IS NOT NULL").fetchone()


def ask_secret(api, attempt, env="ACME_CRM_TOKEN", label="the Acme CRM token", **extra):
    opened = api.post("/api/v2/credential-requests", json={"env": env, "label": label, **extra}, headers=headers(attempt["token"]))
    assert opened.status_code == 200, opened.text
    return opened.json()


def request_status(api, rid):
    with api.app.state.store.read() as c:
        return c.execute("SELECT status FROM credential_requests WHERE id=?", (rid,)).fetchone()[0]


def ops_machine(api):
    machine = runner(api)
    assign(api, machine, "ops")
    post(api, "runners/heartbeat", {"version": "test", "platform": "test", "readiness": {"ops": True}}, machine["token"])
    return machine


def botops_turn(api, title="Connect the Acme CRM for ops"):
    from backend.store import encode
    from backend.tests.test_api import ready
    with api.app.state.store.transaction() as c:
        c.execute("INSERT INTO bots(slug,display_name,state) VALUES('botops','BotOps','active')")
        c.execute("INSERT INTO bot_config(bot,config,operator) VALUES('botops',?, 'ana')",
                  (encode({"name": "botops", "runtime": "fake", "status": "active"}),))
    machine = runner(api)
    assign(api, machine, "botops")
    ready(api, machine, ["botops"])
    t = post(api, "tasks", {"owner": "botops", "title": title, "body": "Get it working."})
    return t, claim(api, machine, "botops")


def botops_ask(api, attempt, bot, env="ACME_CRM_TOKEN"):
    opened = api.post("/api/v2/credential-requests", json={"env": env, "for_bot": bot, "label": f"{bot}'s Acme CRM token",
                                                         "on_behalf_of": "turn"},
                      headers={**headers(attempt["token"]), "X-Tico-On-Behalf-Of": "turn"})
    assert opened.status_code == 200, opened.text
    return opened.json()


def botops_wakes(api, t):
    with api.app.state.store.read() as c:
        return [r["body"] for r in c.execute(
            "SELECT m.body FROM messages m JOIN conversations cv ON cv.id=m.conversation_id "
            f"WHERE {H.MESSAGE_TASK_SQL}=? AND m.to_actor='bot:botops' AND json_extract(m.refs,'$.reconnected') IS NOT NULL",
            (t["id"],))]


def test_botops_asking_on_its_own_task_cards_that_task_which_waits_and_wakes_once(api):
    t, attempt = botops_turn(api)
    opened = botops_ask(api, attempt, "ops")
    [card] = cards(api, t)
    assert card["key"] == "secret:ops:ACME_CRM_TOKEN" and card["bot"] == "ops" and card["status"] == "open"
    assert shown(api, t)["task"]["status"] == "waiting"
    with api.app.state.store.read() as c:
        assert H.message(c, opened["message_id"])["conversation_id"] == H.task(c, t["id"])["conversation_id"]
    post(api, f"credential-requests/{opened['id']}/save", {"value": "crm-token-0123456789"})
    assert botops_wakes(api, t) == [f"Reconnected: {t['title']}\nops's Acme CRM token is saved. Continue."]
    assert shown(api, t)["task"]["status"] in ("open", "doing")


def test_botops_asking_one_variable_for_two_bots_on_one_task_waits_on_both_cards(api):
    t, attempt = botops_turn(api, "Connect the Acme CRM for ops and cpo")
    first, second = botops_ask(api, attempt, "ops"), botops_ask(api, attempt, "cpo")
    assert sorted(c["key"] for c in cards(api, t)) == ["secret:cpo:ACME_CRM_TOKEN", "secret:ops:ACME_CRM_TOKEN"]
    post(api, f"credential-requests/{second['id']}/save", {"value": "crm-token-0123456789"})
    assert sorted(c["status"] for c in cards(api, t)) == ["fixed", "open"]       # cpo's saved, ops's still asked
    assert shown(api, t)["task"]["status"] == "waiting" and botops_wakes(api, t) == []
    post(api, f"credential-requests/{first['id']}/save", {"value": "crm-token-9876543210"})
    [woke] = botops_wakes(api, t)
    assert "ops's Acme CRM token is saved." in woke and "cpo's Acme CRM token is saved." in woke


def test_another_person_asked_on_the_same_task_gets_a_card_that_replaces_the_first(api):
    machine = ops_machine(api)
    t = task(api)
    attempt = claim(api, machine)
    first = ask_secret(api, attempt)                                    # for the bot's operator, Ana
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE bot_config SET operator='ben' WHERE bot='ops'")
    second = ask_secret(api, attempt)                                   # Ben runs the bot now: his card
    assert second["id"] != first["id"] and request_status(api, first["id"]) == "cancelled"
    assert sorted((c["person"], c["status"]) for c in cards(api, t)) == [("human:ana", "retired"), ("human:ben", "open")]
    post(api, f"credential-requests/{second['id']}/save", {"value": "crm-token-0123456789"}, "ben-test")
    assert len(wakes(api, t)) == 1 and shown(api, t)["task"]["status"] in ("open", "doing")


def test_a_fix_owed_when_the_task_changes_hands_never_reaches_the_next_owner(api):
    t = task(api)
    with api.app.state.store.transaction() as c:
        child = H.task_create(c, "bot:ops", "Get the Acme price list", "x", "bot:cpo", parent_id=t["id"])["id"]
    open_card(api, t)
    with api.app.state.store.transaction() as c:
        assert R.google_connected(c, "ana@acme.example") == []           # owed: the child still holds it
        H.task_update(c, "human:ana", t["id"], owner="bot:finance")
        H.task_update(c, "bot:cpo", child, status="done", note="Sent")
        told = [r["body"] for r in c.execute("SELECT body FROM messages WHERE to_actor='bot:finance'")]
    assert told and not any("reconnected" in b for b in told)


def test_two_fixers_on_one_task_each_have_their_own_item(api):
    t = task(api)
    open_card(api, t)
    with api.app.state.store.transaction() as c:
        R.open(c, t["id"], "ops", "secret:ops:ACME_CRM_TOKEN", "secret", "the Acme CRM token", "human:ben", {"kind": "secret"})
    for token, key in (("ana-test", "google:ana@acme.example"), ("ben-test", "secret:ops:ACME_CRM_TOKEN")):
        [item] = [i for i in needs(api, token) if i["id"] == t["id"]]
        assert item["reconnect"]["key"] == key


def test_a_credential_card_whose_task_finishes_or_changes_hands_can_no_longer_be_saved(api):
    machine = ops_machine(api)
    for end in ("close", "reassign"):
        t = task(api, f"Reply to Acme ({end})")
        attempt = claim(api, machine)
        asked = ask_secret(api, attempt)
        post(api, f"attempts/{attempt['id']}/started", {"thread_id": end}, machine["token"])
        post(api, f"attempts/{attempt['id']}/complete", {"outcome": "completed", "last_seq": 0}, machine["token"])
        with api.app.state.store.transaction() as c:
            if end == "close":
                H.task_close(c, "human:ana", t["id"], "Not needed")
            else:
                H.task_update(c, "human:ana", t["id"], owner="bot:cpo")
        assert [c["status"] for c in cards(api, t)] == ["retired"] and request_status(api, asked["id"]) == "cancelled"
        saved = api.post(f"/api/v2/credential-requests/{asked['id']}/save", json={"value": "crm-token-0123456789"},
                         headers=headers("ana-test"))
        assert saved.status_code >= 400 and request_status(api, asked["id"]) == "cancelled"


def test_a_private_task_cards_a_fixer_who_can_read_it_and_never_one_who_cannot(api, monkeypatch):
    machine = codex_bot(api)
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE bot_config SET operator='cara' WHERE bot='ops'")           # cara cannot read it
    t = post(api, "tasks", {"owner": "ops", "title": "Price the Acme deal", "body": "x", "private": True}, "ben-test")
    attempt = claim(api, machine)
    # Its turn may not ask for a credential at all: refused, not a card nobody can see.
    asked = api.post("/api/v2/credential-requests", json={"env": "ACME_CRM_TOKEN", "label": "the Acme CRM token"},
                     headers=headers(attempt["token"]))
    assert asked.status_code == 403
    refused(api, machine, attempt)
    [card] = cards(api, t)
    assert card["person"] == "human:ana"                                             # the company owner can read it
    assert shown(api, t, "ben-test")["task"]["status"] == "waiting"
    monkeypatch.setattr(api.app.state.store.settings, "owner_email", "cara@acme.example")
    with api.app.state.store.transaction() as c:
        row = H.task(c, t["id"])
        assert R.person_for(c, api.app.state.store.settings, "ops", row) is None    # nobody who fixes it reads it
        assert R.person_for(c, api.app.state.store.settings, "ops") == "human:cara"


def make_private(api, t):
    with api.app.state.store.transaction() as c:
        H.task_update(c, "human:ben", t["id"], private=True)


def test_a_task_made_private_retires_a_card_nobody_else_can_fix_and_stops_waiting_on_it(api):
    t = post(api, "tasks", {"owner": "ops", "title": "Price the Acme deal", "body": "x"}, "ben-test")
    open_card(api, t, "google:cara@acme.example", "human:cara")              # only Cara connects her mailbox
    assert [i["id"] for i in needs(api, "cara-test") if i.get("reconnect")] == [t["id"]]
    make_private(api, t)                                                     # Cara cannot read it now
    assert [c["status"] for c in cards(api, t)] == ["retired"]
    assert shown(api, t, "ben-test")["task"]["status"] in ("open", "doing")
    assert not [i for i in needs(api, "cara-test") if i.get("reconnect")]


def test_a_task_made_private_moves_a_login_card_to_a_fixer_who_can_read_it(api):
    machine = codex_bot(api)
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE bot_config SET operator='cara' WHERE bot='ops'")
    t = post(api, "tasks", {"owner": "ops", "title": "Price the Acme deal", "body": "x"}, "ben-test")
    refused(api, machine, claim(api, machine))
    [first] = cards(api, t)
    assert first["person"] == "human:cara"
    make_private(api, t)
    assert sorted((c["person"], c["status"]) for c in cards(api, t)) == [("human:ana", "open"), ("human:cara", "retired")]
    assert shown(api, t, "ben-test")["task"]["status"] == "waiting"
    assert [i["reconnect"]["person"] for i in needs(api) if i["id"] == t["id"]] == ["human:ana"]
    heartbeat(api, machine, True)                                             # the fix still reaches the task
    assert len(wakes(api, t)) == 1


def test_a_former_reader_cannot_load_or_save_the_credential_card_by_id(api):
    machine = ops_machine(api)
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE bot_config SET operator='cara' WHERE bot='ops'")
    t = post(api, "tasks", {"owner": "ops", "title": "Price the Acme deal", "body": "x"}, "ben-test")
    asked = ask_secret(api, claim(api, machine))
    get(api, f"credential-requests/{asked['id']}", "cara-test")
    make_private(api, t)
    assert request_status(api, asked["id"]) == "cancelled"
    get(api, f"credential-requests/{asked['id']}", "cara-test", expected=404)
    post(api, f"credential-requests/{asked['id']}/save", {"value": "crm-token-0123456789"}, "cara-test", expected=404)


def test_asking_again_on_one_task_reuses_its_card_and_another_task_keeps_its_own(api):
    machine = ops_machine(api)
    first = task(api, "Reply to Acme")
    a1 = claim(api, machine)
    one = ask_secret(api, a1)
    assert ask_secret(api, a1)["id"] == one["id"]                 # the same task asks again: the same card
    assert len(cards(api, first)) == 1 and request_status(api, one["id"]) == "pending"
    post(api, f"attempts/{a1['id']}/started", {"thread_id": "x"}, machine["token"])
    post(api, f"attempts/{a1['id']}/complete", {"outcome": "completed", "last_seq": 0}, machine["token"])
    second = task(api, "Book the Acme call")
    two = ask_secret(api, claim(api, machine))
    # A second task's card does not cancel the first one's, which its task still waits on.
    assert two["id"] != one["id"] and request_status(api, one["id"]) == request_status(api, two["id"]) == "pending"
    post(api, f"credential-requests/{two['id']}/save", {"value": "crm-token-0123456789"})
    assert [c["status"] for c in cards(api, first) + cards(api, second)] == ["fixed", "fixed"]
    assert request_status(api, one["id"]) == "saved"              # saved once, for every task that asked
    assert len(wakes(api, first)) == len(wakes(api, second)) == 1


def test_cancelling_the_card_ends_the_wait_with_one_wake_that_says_so(api):
    machine = ops_machine(api)
    t = task(api)
    opened = ask_secret(api, claim(api, machine))
    post(api, f"credential-requests/{opened['id']}/cancel", {})
    assert [c["status"] for c in cards(api, t)] == ["cancelled"]
    assert shown(api, t)["task"]["status"] in ("open", "doing")
    [woke] = wakes(api, t)
    assert "cancelled the card for the Acme CRM token" in woke
    assert not [i for i in needs(api) if i["id"] == t["id"]]


def test_two_cards_on_one_task_wake_it_once_when_the_last_is_fixed(api):
    t = task(api)
    open_card(api, t, key="google:ana@acme.example")
    with api.app.state.store.transaction() as c:
        R.open(c, t["id"], "ops", "secret:ops:ACME_CRM_TOKEN", "secret", "the Acme CRM token", "human:ana", {"kind": "secret"})
        assert R.google_connected(c, "ana@acme.example") == []
        assert H.task(c, t["id"])["status"] == "waiting"
    assert wakes(api, t) == []
    with api.app.state.store.transaction() as c:
        assert R.secret_stored(c, "ops", "ACME_CRM_TOKEN") == [t["id"]]
    [woke] = wakes(api, t)
    assert "Google (ana@acme.example) is reconnected." in woke and "the Acme CRM token is saved." in woke


def test_a_fix_while_another_hold_stands_is_told_once_when_that_hold_clears(api):
    t = task(api)
    with api.app.state.store.transaction() as c:
        child = H.task_create(c, "bot:ops", "Get the Acme price list", "x", "bot:cpo", parent_id=t["id"])["id"]
    open_card(api, t)
    with api.app.state.store.transaction() as c:
        assert R.google_connected(c, "ana@acme.example") == []
        assert H.task(c, t["id"])["status"] == "waiting"
    assert wakes(api, t) == []
    with api.app.state.store.transaction() as c:
        H.task_update(c, "bot:cpo", child, status="done", note="Sent")
        assert H.task(c, t["id"])["status"] in ("open", "doing")
    [woke] = wakes(api, t)
    assert "Google (ana@acme.example) is reconnected." in woke
    with api.app.state.store.transaction() as c:
        assert R.google_connected(c, "ana@acme.example") == []
    assert len(wakes(api, t)) == 1


def test_a_fix_while_several_holds_stand_is_told_with_the_wake_the_last_one_clearing_sends(api):
    t = task(api)
    with api.app.state.store.transaction() as c:
        first, second = (H.task_create(c, "bot:ops", title, "x", "bot:cpo", parent_id=t["id"])["id"]
                         for title in ("Get the Acme price list", "Get the Acme contract"))
    open_card(api, t)

    def told():
        with api.app.state.store.read() as c:
            return [r[0] for r in c.execute("SELECT body FROM messages WHERE to_actor='bot:ops' AND body LIKE '%is reconnected%'")]
    with api.app.state.store.transaction() as c:
        assert R.google_connected(c, "ana@acme.example") == []
        H.task_update(c, "bot:cpo", first, status="done", note="Sent")
        assert H.task(c, t["id"])["status"] == "waiting"
        # Any other wake to the owner while a hold stands leaves the news owed.
        assert H._wake(c, H.task(c, t["id"]), "bot:ops", "The Acme price list is in.")
    assert told() == []
    with api.app.state.store.transaction() as c:
        H.task_update(c, "bot:cpo", second, status="done", note="Sent")
        assert H.task(c, t["id"])["status"] in ("open", "doing")
    [woke] = told()
    assert "Google (ana@acme.example) is reconnected." in woke and "Get the Acme contract is done" in woke


def hold_item(api, bot="ops"):
    from backend import run_backoff
    with api.app.state.store.transaction() as c:
        run_backoff.ensure(c)
        c.execute("INSERT INTO run_backoff(bot,fails,error_class,error) VALUES(?,5,?,?) ON CONFLICT(bot) DO NOTHING",
                  (bot, run_backoff.SIGN_IN, "401 unauthorized"))
        run_backoff.hold(c, api.app.state.store.settings, bot, 5, run_backoff.SIGN_IN, "401 unauthorized", H.now())
        return run_backoff.get(c, bot).get("task_id")


def login_card(api, t, runner_id="r1"):
    with api.app.state.store.transaction() as c:
        c.execute("INSERT INTO runners(id,label,token_hash,operator,created_at) VALUES(?,?,?,?,?) ON CONFLICT DO NOTHING",
                  (runner_id, "Acme Mac", "x-" + runner_id, "ana", H.now()))
        return R.login_refused(c, api.app.state.store.settings, "ops", runner_id, "codex", t["id"])


def test_only_a_standing_login_card_stands_for_the_sign_in_hold_item(api):
    t = task(api)
    open_card(api, t)                                        # a Google card is another cause
    assert hold_item(api)
    from backend import run_backoff
    with api.app.state.store.transaction() as c:
        run_backoff.release(c, H.KEEPER, "ops")
    second = task(api, "Book the Acme call")
    assert login_card(api, second)
    assert hold_item(api) is None                            # its card is the item
    with api.app.state.store.transaction() as c:
        H.task_close(c, "human:ana", second["id"], "Not needed")
        assert not R.bot_open(c, "ops", "login:")
        assert run_backoff.get(c, "ops").get("task_id")      # the card went unfixed: the hold's own item is back
    assert [c["status"] for c in cards(api, second)] == ["retired"]


def test_a_card_retires_when_its_task_changes_hands_or_its_bot_is_archived_and_a_later_fix_wakes_nobody(api):
    moved, kept = task(api, "Reply to Acme"), task(api, "Book the Acme call")
    open_card(api, moved)
    open_card(api, kept)
    with api.app.state.store.transaction() as c:
        H.task_update(c, "human:ana", moved["id"], owner="bot:cpo")
        assert R.pending(c, moved["id"]) is None
        from backend import settings_admin
        settings_admin.archive_bot(c, "human:ana", "ops")
        assert R.pending(c, kept["id"]) is None
        assert R.google_connected(c, "ana@acme.example") == []
    assert [c["status"] for c in cards(api, moved) + cards(api, kept)] == ["retired", "retired"]
    assert wakes(api, moved) == wakes(api, kept) == []
    assert not [i for i in needs(api) if i.get("reconnect")]
