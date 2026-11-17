"""Which mailbox a message bot's token is for.

A message bot's mailbox is the address its `gmail` tool declares in bot.yaml (BotOps fills it from the
`Mailbox:` line). Only when it declares none does its person's own email stand in.
"""

from backend import mailbox_access, routines
from backend.tests.helpers.api import post
from backend.tests.helpers.mail_api import seed_org
from backend.tests.helpers.mail_targets import declare

DECLARED = "ana@acme.team"


def assigned(api):
    """Ana's message bot set in People (backend/access.py assign_message_bot): the grant on the mailbox it reads."""
    with api.app.state.store.transaction() as c:
        mailbox_access.message_bot_changed(c, {"id": "ana", "email": "ana@acme.example"}, None, "", "inbox")


def test_a_message_bots_token_covers_the_mailbox_it_was_given_not_one_it_only_declares(api):
    seed_org(api)
    declare(api, DECLARED)
    with api.app.state.store.read() as c:
        assert routines.token_mailboxes(c, "inbox") == []
    assigned(api)
    with api.app.state.store.read() as c:
        assert routines.token_mailboxes(c, "inbox") == [DECLARED]
        assert routines.token_mailboxes(c, "assistant") == []


def test_org_read_in_bot_yaml_reads_no_reports_mail(api):
    """Ana has reports on the roster. `org_read: true` in a gmail entry once read their mail; now only a grant does
    (the migration to mailboxes, 0092, turned each org_read into Read grants)."""
    seed_org(api)
    for setting in ({}, {"org_read": False}, {"org_read": "true"}, {"org_read": True}):
        declare(api, DECLARED, **setting)
        assigned(api)
        with api.app.state.store.read() as c:
            assert routines.token_mailboxes(c, "inbox") == [DECLARED], setting
            assert routines.inbox_of(c, "inbox") == {"mailboxes": [DECLARED]}
            assert routines.token_mailboxes(c, "assistant") == []


def named(api, person, body, token="ana-test", expected=200):
    return post(api, f"access/humans/{person}", body, token=token, expected=expected)


def test_only_an_owner_or_admin_names_a_message_bot_and_one_bot_serves_one_person(api):
    seed_org(api)
    named(
        api, "ben", {"inbox_bot": "assistant", "mailbox": "ben@acme.team"}, token="cara-test", expected=403
    )  # a member cannot
    named(api, "ben", {"inbox_bot": "inbox"}, expected=409)  # Ana's message bot
    named(api, "ben", {"mailbox": "ben@acme.team"}, expected=422)  # an address needs the bot that reads it
    named(api, "ben", {"inbox_bot": "no-such-bot"}, expected=404)
    named(api, "ben", {"inbox_bot": "assistant", "mailbox": "not an address"}, expected=422)
    with api.app.state.store.read() as c:
        assert routines.token_mailboxes(c, "assistant") == []
