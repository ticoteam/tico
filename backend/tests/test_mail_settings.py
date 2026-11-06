"""A bot's "send without approval" switch is its layer of the one sending policy, set only by a person who manages the
bot (backend/mail_settings.py over backend/email_sending.py). A bot, or BotOps in its own run or acting for a person,
cannot set it, and bot.yaml asks for nothing.
"""

from backend import email_sending as S
from backend.tests.test_api import api, get, headers, setup_attempt  # noqa: F401
from backend.tests.test_botops_parity import _wake, act
from backend.tests.test_member_bots import botops, finish, turn  # noqa: F401  (fixture)


def setting(api, token="ana-test", bot="ops"):
    return get(api, f"bots/{bot}/mail-settings", token=token)


def change(api, body, token="ana-test", bot="ops"):
    return api.post(f"/api/v2/bots/{bot}/mail-settings", json=body, headers=headers(token))


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

    # Forward targets are retired: a forward is a send, decided and approved like any other.
    assert change(api, {"outbound_send": True, "forward_to": ["me@personal.example"]}, "ben-test").status_code == 422
    r = change(api, {"outbound_send": True}, "ben-test")
    assert r.status_code == 200, r.text
    seen = setting(api, attempt["token"])
    assert (seen["set"], seen["outbound_send"], seen["forward_to"], seen["updated_by"]) == (True, True, [], "human:ben")
    assert seen["sending_policy"]["mode"] == "on_their_own"
    with api.app.state.store.read() as c:
        assert S.saved(c, "bot:ops")["mode"] == "on_their_own" and S.sends_on_own(c, "ops")
    # Off is Ask first, never Never: the bot still sends what a person approves.
    assert change(api, {"outbound_send": False}, "ben-test").status_code == 200
    with api.app.state.store.read() as c:
        assert S.saved(c, "bot:ops")["mode"] == "ask_first" and not S.sends_on_own(c, "ops")


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
