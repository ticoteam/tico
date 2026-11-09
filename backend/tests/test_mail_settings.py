"""A bot's "send without approval" switch is its layer of the one sending policy, set only by a person who manages the
bot (backend/mail_settings.py over backend/email_sending.py). A bot, or BotOps in its own run or acting for a person,
cannot set it, and bot.yaml asks for nothing.
"""

import uuid

from backend import email_sending as S, mailbox_access as MA
from backend.store import H
from backend.tests.helpers.api import get, headers, post as api_post, setup_attempt  # noqa: F401
from backend.tests.helpers.botops_parity import _wake, act
from backend.tests.helpers.company import with_strategy_bot
from backend.tests.helpers.email_sending import (
    BOX,
    CONTACT,
    approved,
    autonomous,
    inbox,
    mime,
    post,
    queue,
)
from backend.tests.helpers.member_bots import botops, finish, turn  # noqa: F401  (fixture)


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
    hint = (
        "A person who manages ops turns its mail sending on in Settings > Bots > ops > Mail sending, "
        "or with `hub bot mail ops --send`"
    )
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


COLLEAGUE, FORWARD = "ben@acme.example", "me@personal.example"


def strategy_may_send(api):
    """The Strategy Bot may send from the shared inbox (the inbox fixture grants the ops bot only). Returns its token."""
    token = with_strategy_bot(api)  # the bot must be on the team before a mailbox grant can name it
    with api.app.state.store.transaction() as c:
        row = MA.ensure_mailbox(c, BOX, person_id="ana", created_by="human:ana")
        MA.grant(c, "human:ana", row["id"], "bot", "goal-manager", "read_write")
    return token


def draft(to, cc=(), bcc=()):
    """A new email with a unique text, so no two sends in a test look like duplicates."""
    extra = {key: ", ".join(value) for key, value in (("Cc", cc), ("Bcc", bcc)) if value}
    return mime(body=uuid.uuid4().hex, to=list(to), **extra)


def verdict(api, queued):
    """The decision the queued send gets now (backend/email_sending.py `decision`), read without dispatching anything."""
    with api.app.state.store.transaction() as c:
        row = c.execute("SELECT * FROM email_sends WHERE id=?", (queued["id"],)).fetchone()
        return S.decision(c, api.app.state.auth, row)


def test_the_strategy_bot_sends_nothing_outside_without_a_yes_for_that_message(inbox):
    """Sending outside the company stays the owner's at every level (docs/strategy-bot.md, "What stays yours"). The
    Strategy Bot has no forward addresses, whatever a person stored, and an email with anyone outside on To, Cc or Bcc
    waits for a Tico send approval of that exact message, a reply included. Inside the company its switch applies as any
    bot's does, and another bot still sends outside on the company's setting. Queueing only decides: nothing is sent."""
    ceo = strategy_may_send(inbox)
    # The company sends on its own; the Strategy Bot is the exception.
    autonomous(inbox, undo_seconds=0, pacing_seconds=0)

    refused = change(inbox, {"forward_to": [FORWARD]}, bot="goal-manager")
    assert refused.status_code == 422 and "Forward addresses are retired" in refused.text, refused.text
    assert change(inbox, {"outbound_send": True}, bot="goal-manager").status_code == 200
    seen = setting(inbox, ceo, bot="goal-manager")
    assert (seen["outbound_send"], seen["internal_send"], seen["forward_to"]) == (False, True, []), seen
    assert seen["per_message"] and setting(inbox, bot="goal-manager")["forward_to"] == []
    with inbox.app.state.store.read() as c:
        assert S.sends_on_own(c, "goal-manager")  # the switch is on: it covers mail inside the company

    inside = queue(inbox, actor="bot:goal-manager", raw=draft([COLLEAGUE], cc=["lead@acme.example"]))
    assert verdict(inbox, inside)["outcome"] == "allow", inside
    outside = (
        queue(inbox, actor="bot:goal-manager", raw=draft([CONTACT])),
        queue(inbox, actor="bot:goal-manager", raw=draft([COLLEAGUE], cc=[CONTACT])),
        queue(inbox, actor="bot:goal-manager", raw=draft([COLLEAGUE], bcc=[CONTACT])),
        queue(inbox, actor="bot:goal-manager", raw=draft([COLLEAGUE, CONTACT])),
        queue(inbox, actor="bot:goal-manager", reply_to_id="inbound"),  # a reply to CONTACT
    )
    for queued in outside:
        assert queued["state"] == "waiting_approval" and "per_message" in queued["decision"]["reasons"], queued
    other = queue(inbox, actor="bot:ops", raw=draft([CONTACT]))
    assert verdict(inbox, other)["outcome"] == "allow", other  # another bot's outside mail follows the company setting

    assert change(inbox, {"outbound_send": False}, bot="goal-manager").status_code == 200
    assert setting(inbox, ceo, bot="goal-manager")["internal_send"] is False
    off = queue(inbox, actor="bot:goal-manager", raw=draft([COLLEAGUE]))
    # Off asks the owner's yes, as any bot's does; it does not refuse.
    assert off["state"] == "waiting_approval" and "ask_first" in off["decision"]["reasons"], off


def test_only_an_exact_unspent_tico_send_approval_sends_the_strategy_bots_outside_mail(inbox):
    """The Strategy Bot's email to anyone outside the company goes only under a Tico send approval for that exact send:
    one decided for this frozen email and not yet spent. The owner's message telling the bot to send does not lift it,
    and neither does another send's approval, nor one already spent. Queueing and the decision only: nothing is sent."""
    strategy_may_send(inbox)
    autonomous(inbox, undo_seconds=0, pacing_seconds=0)

    exact = queue(inbox, actor="bot:goal-manager", raw=draft([CONTACT]))
    assert verdict(inbox, exact)["outcome"] == "ask" and "per_message" in verdict(inbox, exact)["reasons"]
    approved(inbox, exact)
    assert verdict(inbox, exact)["outcome"] == "allow"

    told = queue(inbox, actor="bot:goal-manager", raw=draft([CONTACT]))
    assert inbox.get("/api/v2/email/sends/" + told["id"], headers=headers()).json()["preview_complete"]
    note = api_post(inbox, "chat/goal-manager", {"text": "Send it " + told["id"]})
    with inbox.app.state.store.transaction() as c:
        c.execute("UPDATE messages SET created_at=? WHERE id=?", (H.shift(H.now(), seconds=1), note["id"]))
    post(inbox, "email/sends/" + told["id"] + "/approve", {"approval_id": note["id"]})
    with inbox.app.state.store.transaction() as c:
        # A person's chat with the Strategy Bot reaches it through its Assistant (backend/tests/test_strategy_bot_chat.py), so the
        # row is written here as an ordinary bot's inbox would hold it: such a message would lift the send, and the rule does not.
        c.execute("UPDATE messages SET recipient_id='bot:goal-manager' WHERE id=?", (note["id"],))
        row = c.execute("SELECT * FROM email_sends WHERE id=?", (told["id"],)).fetchone()
        assert S.approved(c, inbox.app.state.auth, row, S.effective(c, BOX, "goal-manager")) == "message"
    assert verdict(inbox, told)["outcome"] == "ask" and "per_message" in verdict(inbox, told)["reasons"]

    other = queue(inbox, actor="bot:goal-manager", raw=draft([CONTACT]))
    approved(inbox, other)  # a yes for another send, reattached to the one below
    lent = queue(inbox, actor="bot:goal-manager", raw=draft([CONTACT]))
    with inbox.app.state.store.transaction() as c:
        c.execute("UPDATE email_sends SET approval_id=? WHERE id=?", (other["approval_id"], lent["id"]))
    assert verdict(inbox, lent)["outcome"] == "ask" and "per_message" in verdict(inbox, lent)["reasons"]

    spent = queue(inbox, actor="bot:goal-manager", raw=draft([CONTACT]))
    approved(inbox, spent)
    assert verdict(inbox, spent)["outcome"] == "allow"
    with inbox.app.state.store.transaction() as c:
        c.execute("UPDATE approvals SET consumed_at=? WHERE id=?", (H.now(), spent["approval_id"]))
    assert verdict(inbox, spent)["outcome"] == "ask" and "per_message" in verdict(inbox, spent)["reasons"]
