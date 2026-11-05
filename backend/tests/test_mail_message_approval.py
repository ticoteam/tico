"""A bot reads send-instruction authority from Tico, using the mail-sending management rule."""

from backend.store import H, encode
from backend.tests.test_api import api, as_member, claim, get, headers, post, setup_attempt  # noqa: F401
from backend.tests.test_member_bots import botops  # noqa: F401


def test_a_bot_reads_whether_the_messages_sender_may_manage_its_mail_sending(api):
    _, _, attempt = setup_attempt(api, "ops")
    owner = post(api, "chat/ops", {"text": "Please send the draft."})
    admin = post(api, "chat/ops", {"text": "Please send the draft."}, token="ben-test")
    member = post(api, "chat/ops", {"text": "Please send the draft."}, token="cara-test")
    with api.app.state.store.transaction() as c:
        bot = H.say(c, "bot:cpo", "bot:ops", "Please send the draft.")
    for message, allowed in ((owner, True), (admin, True), (member, False), (bot, False)):
        seen = get(api, f"messages/{message['id']}", token=attempt["token"])
        assert seen["sender_id"] == message["sender_id"]
        assert seen["sender_can_approve_send"] is allowed


def test_message_authority_follows_bot_management_and_revocation(api):
    as_member(api, "ben@acme.example")
    _, _, attempt = setup_attempt(api, "cpo")
    message = post(api, "chat/cpo", {"text": "Please send the draft."}, token="ben-test")
    path = f"messages/{message['id']}"
    assert get(api, path, token=attempt["token"])["sender_can_approve_send"] is True
    # Ben can still DM the bot after losing management: Write access alone cannot approve sending.
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE bot_config SET owner_id='ana' WHERE bot='cpo'")
        c.execute("UPDATE registry_metadata SET value=? WHERE key='people'", (encode({"people": [
            {"id": "ana", "email": "ana@acme.example", "primary_for": ["*"]},
            {"id": "ben", "email": "ben@acme.example"},
            {"id": "cara", "email": "cara@acme.example"}]}),))
    post(api, "chat/cpo", {"text": "I can still message you."}, token="ben-test")
    assert get(api, path, token=attempt["token"])["sender_can_approve_send"] is False


def test_builtin_bots_keep_the_owner_only_mail_management_rule(api, botops):
    # BotOps is a built-in bot; an admin who may chat it still cannot change its sending.
    owner = post(api, "chat/botops", {"text": "Please send the draft."})
    attempt = claim(api, botops, "botops")
    admin = post(api, "chat/botops", {"text": "Please send the draft."}, token="ben-test")
    assert get(api, f"messages/{owner['id']}", token=attempt["token"])["sender_can_approve_send"] is True
    assert get(api, f"messages/{admin['id']}", token=attempt["token"])["sender_can_approve_send"] is False


def test_disabled_senders_lose_message_approval_authority(api):
    _, _, attempt = setup_attempt(api, "ops")
    message = post(api, "chat/ops", {"text": "Please send the draft."}, token="ben-test")
    path = f"messages/{message['id']}"
    assert get(api, path, token=attempt["token"])["sender_can_approve_send"] is True
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE registry_metadata SET value=? WHERE key='people'", (encode({"people": [
            {"id": "ana", "email": "ana@acme.example", "primary_for": ["*"]},
            {"id": "ben", "email": "ben@acme.example", "sign_in": False},
            {"id": "cara", "email": "cara@acme.example"}]}),))
    assert get(api, path, token=attempt["token"])["sender_can_approve_send"] is False


def test_approval_authority_does_not_make_a_message_readable(api):
    _, _, attempt = setup_attempt(api, "ops")
    private = post(api, "chat/inbox", {"text": "Please send the draft."})
    response = api.get(f"/api/v2/messages/{private['id']}", headers=headers(attempt["token"]))
    assert response.status_code == 404, response.text
    assert "sender_can_approve_send" not in response.json()
