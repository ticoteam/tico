"""Mail sending without approval is a server-held switch only a person who manages the bot sets (backend/mail_settings.py).

bot.yaml's `outbound_send` and `forward_to` only ask: a bot, or BotOps in its own run or acting for a person, cannot
set the switch, and Health says when a bot.yaml asks for more than a person approved.
"""

from backend.tests.test_api import api, assign, claim, get, headers, post, runner, setup_attempt  # noqa: F401
from backend.tests.test_botops_parity import _wake, act
from backend.tests.test_member_bots import botops, finish, turn  # noqa: F401  (fixture)
from runner import declared_access

RUNTIMES = {"codex": {"installed": True, "authenticated": "ready"}}


def setting(api, token="ana-test", bot="ops"):
    return get(api, f"bots/{bot}/mail-settings", token=token)


def change(api, body, token="ana-test", bot="ops"):
    return api.post(f"/api/v2/bots/{bot}/mail-settings", json=body, headers=headers(token))


def report(api, machine, bot, manifest):
    row = {"ready": True, "runtime": "codex", "model": "gpt-6-luna", "repository_present": True,
           "configuration_valid": True, "problems": []}
    asked = declared_access.mail_request(manifest)
    if asked:
        row["mail_request"] = asked
    r = api.post("/api/v2/runners/heartbeat", headers=headers(machine["token"]), json={
        "version": "test", "platform": "test", "readiness": {"schema_version": 1, "runtimes": RUNTIMES, "bots": {bot: row}}})
    assert r.status_code == 200, r.text


def mail_alert(api):
    checks = get(api, "health")["checks"]
    return next((c["summary"] for c in checks if c["id"] == "mail_sending"), None)


def test_only_a_person_who_manages_the_bot_turns_sending_on(api, botops):
    assert setting(api) == {**setting(api), "set": False, "outbound_send": False, "forward_to": []}
    _, _, attempt = setup_attempt(api, "ops")
    # The bot reads its own setting (its mail connector does) but never sets it.
    assert setting(api, attempt["token"])["set"] is False
    assert change(api, {"outbound_send": True}, attempt["token"]).status_code == 403
    # Nor BotOps: not in its own run (the daily update), not as the person who asked it.
    own = _wake(api, botops)
    assert act(api, own, "POST", "bots/ops/mail-settings", {"outbound_send": True}, ref=None).status_code == 403
    finish(api, botops, own)
    lent = turn(api, botops, person="ana-test", text="Let ops send mail")
    assert act(api, lent, "POST", "bots/ops/mail-settings", {"outbound_send": True}).status_code == 403
    # A person who does not manage the bot is refused too.
    assert change(api, {"outbound_send": True}, "cara-test").status_code == 403
    assert setting(api)["set"] is False

    assert change(api, {"forward_to": ["not an address"]}).status_code == 422
    r = change(api, {"outbound_send": True, "forward_to": ["Me@Personal.example"]}, "ben-test")
    assert r.status_code == 200, r.text
    seen = setting(api, attempt["token"])
    assert (seen["set"], seen["outbound_send"], seen["forward_to"], seen["updated_by"]) == \
        (True, True, ["me@personal.example"], "human:ben")


def test_health_alerts_while_bot_yaml_asks_for_more_than_a_person_approved(api):
    machine = runner(api)
    assign(api, machine, "ops")
    report(api, machine, "ops", {"outbound_send": True, "forward_to": ["me@personal.example"]})
    assert "ops asks to send mail without approval and to forward mail to me@personal.example; " \
           "a person must turn this on" in mail_alert(api)
    assert setting(api)["unapproved"] == {"outbound_send": True, "forward_to": ["me@personal.example"]}

    # The upgrade step: a person reads the request and takes it.
    assert change(api, {"from_request": True}).status_code == 200
    assert setting(api)["outbound_send"] is True and mail_alert(api) is None

    # A forward target added to bot.yaml later is a new request, not a new target.
    report(api, machine, "ops", {"outbound_send": True, "forward_to": ["me@personal.example", "x@elsewhere.example"]})
    assert setting(api)["forward_to"] == ["me@personal.example"]
    assert "ops asks to forward mail to x@elsewhere.example" in mail_alert(api)


def test_a_manager_sees_the_alert_for_the_bots_they_manage_and_no_other(api):
    from backend.tests.test_api import as_member
    # Ben operates cpo and not ops; as a plain member (not an admin) he manages cpo only. Cara manages nothing.
    as_member(api, "ben@acme.example")
    machine = runner(api)
    assign(api, machine, "ops")
    assign(api, machine, "cpo")
    rows = {}
    for bot in ("ops", "cpo"):
        rows[bot] = {"ready": True, "runtime": "codex", "model": "gpt-6-luna", "repository_present": True,
                     "configuration_valid": True, "problems": [],
                     "mail_request": declared_access.mail_request({"outbound_send": True})}
    r = api.post("/api/v2/runners/heartbeat", headers=headers(machine["token"]), json={
        "version": "test", "platform": "test", "readiness": {"schema_version": 1, "runtimes": RUNTIMES, "bots": rows}})
    assert r.status_code == 200, r.text

    def seen(token):
        checks = get(api, "health", token=token)["checks"]
        return next((c["summary"] for c in checks if c["id"] == "mail_sending"), None)

    owner = seen("ana-test")
    assert "ops asks to send mail without approval" in owner and "cpo asks to send mail without approval" in owner
    ben = seen("ben-test")
    assert ben == "cpo asks to send mail without approval; a person must turn this on."
    assert seen("cara-test") is None


def test_a_refused_botops_write_names_the_person_and_the_place_not_go_ahead(api, botops):
    hint = ("A person who manages ops turns its mail sending on in Settings > Bots > ops > Mail sending, "
            "or with `hub bot mail ops --send`")
    own = _wake(api, botops)
    r = act(api, own, "POST", "bots/ops/mail-settings", {"outbound_send": True}, ref=None)
    assert r.status_code == 403 and r.json()["error"]["fix"] == hint, r.text
    assert "go ahead" not in r.text and r.json()["error"]["link"].endswith("#/settings")
    finish(api, botops, own)
    lent = turn(api, botops, person="ana-test", text="Let ops send mail")
    r = act(api, lent, "POST", "bots/ops/mail-settings", {"outbound_send": True})
    assert r.status_code == 403 and r.json()["error"]["fix"] == hint and "go ahead" not in r.text, r.text
    # Any other caller that is not a person gets the same pointer in the refusal itself.
    _, _, attempt = setup_attempt(api, "ops")
    r = change(api, {"outbound_send": True}, attempt["token"])
    assert r.status_code == 403 and "Settings > Bots > ops > Mail sending" in r.json()["error"]["detail"]
    assert "hub bot mail ops --send" in r.json()["error"]["detail"]
