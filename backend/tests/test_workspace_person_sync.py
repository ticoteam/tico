"""A Whole Workspace mailbox is read by the server, live: no computer syncs it, and a bot reads it only if granted."""

import pytest

from backend import mailbox_access, routines
from backend.store import encode
from backend.tests.helpers.api import get, setup_attempt  # noqa: F401
from backend.tests.helpers.assistant import assistant_turn
from backend.tests.helpers.gmail_live import gmail_http  # noqa: F401
from backend.tests.helpers.google_workspace_authz import BOX, PERSONAL, workspace_key  # noqa: F401
from backend.tests.helpers.mail_api import ORG, seed_org


@pytest.fixture
def known_person(api, workspace_key):
    seed_org(api)
    with api.app.state.store.transaction() as c:
        return mailbox_access.ensure_mailbox(c, PERSONAL)


def test_a_bot_on_any_computer_gets_no_access_to_a_workspace_mailbox_it_was_not_granted(api, known_person):
    _, _, attempt = setup_attempt(api, "ops")
    assert attempt["mailbox_levels"] == {} and attempt["mailboxes"] == []
    turn = attempt["token"]
    answer = get(api, "mail/access?mailbox=" + PERSONAL, turn)
    assert answer["level"] == "none" and answer["mailboxes"] == []
    get(api, "mail/messages?mailbox=" + PERSONAL, turn, expected=403)
    get(api, "mail/messages/m-plain?mailbox=" + PERSONAL, turn, expected=403)
    with api.app.state.store.read() as c:
        assert PERSONAL not in routines.token_mailboxes(c, "ops")


def test_the_owners_assistant_and_mail_page_read_live_mail_without_a_message_bot(api, workspace_key, gmail_http):
    seed_org(api)
    with api.app.state.store.transaction() as c:
        c.execute(
            "UPDATE registry_metadata SET value=? WHERE key='people'",
            (encode({"people": [{k: v for k, v in p.items() if k != "inbox_bot"} for p in ORG]}),),
        )
        mailbox_access.ensure_mailbox(c, BOX)
    _, attempt = assistant_turn(api, "ana-test", "Read my inbox")
    assert attempt["mailbox_levels"] == {BOX: "read_write"} and attempt["mailboxes"] == [BOX]
    with api.app.state.store.read() as c:
        assert routines.message_bot_mailboxes(c) == []
    for credential in ("ana-test", attempt["token"]):
        boxes = get(api, "mail/mailboxes", credential)["mailboxes"]
        box = next(b for b in boxes if b["address"] == BOX)
        assert box["agent_bot"] is None
        assert get(api, "mail/messages?mailbox=" + BOX, credential)["messages"][0]["msg_id"] == "m2"
        assert get(api, "mail/messages/m1?mailbox=" + BOX, credential)["body"] == "Live contract body."
    answer = get(api, "mail/access?mailbox=" + BOX, attempt["token"])
    assert answer["principal"] == "person:ana" and answer["level"] == "read_write"
