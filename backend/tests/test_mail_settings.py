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
