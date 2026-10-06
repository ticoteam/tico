"""A message bot made for a person is linked to them: `inbox_bot` on their roster entry and the mailbox on the bot.

That link is what lets the bot's runs ask a computer for a mail token (routines.token_mailboxes). It is written where
the bot is made, and once at start for bots made before that when there is no doubt whose they are.
"""
from backend import message_bots, routines
from backend.store import encode
from backend.tests.test_api import api, post  # noqa: F401  (fixture)

MODEL = {"model": "gpt-6.1-sol", "effort": "high", "harness": None, "runner_id": None}


def inbox_bot_of(api, person):
    with api.app.state.store.read() as c:
        return next(p for p in message_bots._roster(c)["people"] if p["id"] == person).get("inbox_bot")


def mailbox_of(api, bot):
    with api.app.state.store.read() as c:
        return routines.declared_mailbox(message_bots._config(c, bot))


def add_from_template(api, slug, instructions, token="ana-test"):
    return post(api, "bots", {"slug": slug, "display_name": "Inbox Manager", "description": "Mail", "status": "planned",
                              "template": "inbox", "instructions": instructions, **MODEL}, token=token)


def test_adding_the_template_in_settings_links_the_person_the_mailbox_line_names(api):
    add_from_template(api, "ben-inbox", "# Inbox\n\nMailbox: ben@acme.example\n")
    assert inbox_bot_of(api, "ben") == "ben-inbox" and inbox_bot_of(api, "ana") is None
    assert mailbox_of(api, "ben-inbox") == "ben@acme.example"
    with api.app.state.store.read() as c:
        assert routines.token_mailboxes(c, "ben-inbox")[0] == "ben@acme.example"


# ------------------------------------------------------------------ start-up backfill

def old_message_bot(api, slug, instructions="", tools=None, template="inbox"):
    """A message bot as an earlier release left it: a template and its text, and nobody's `inbox_bot`."""
    config = {"name": slug, "runtime": "fake", "status": "active", "template": template, "instructions": instructions}
    if tools:
        config["tools"] = tools
    with api.app.state.store.transaction() as c:
        c.execute("INSERT INTO bots(slug,display_name,state) VALUES(?,?,'active')", (slug, slug))
        c.execute("INSERT INTO bot_config(bot,config_json,operator) VALUES(?,?,'ana')", (slug, encode(config)))


def backfill(api):
    with api.app.state.store.transaction() as c:
        return message_bots.backfill(c)


def test_backfill_links_the_bot_whose_mailbox_is_a_person_s_email(api):
    old_message_bot(api, "inbox-manager", "Role.\n\nMailbox: ben@acme.example\n")
    old_message_bot(api, "cara-mail", tools=[{"service": "gmail", "identity": "cara@acme.example", "can": ["read"]}])
    assert backfill(api) == ["cara-mail", "inbox-manager"]
    assert inbox_bot_of(api, "ben") == "inbox-manager" and inbox_bot_of(api, "cara") == "cara-mail"
    assert mailbox_of(api, "inbox-manager") == "ben@acme.example"
    assert backfill(api) == []                                                            # nothing left to do


def test_backfill_leaves_what_is_a_choice_for_an_owner(api):
    old_message_bot(api, "first", "Mailbox: ben@acme.example\n")
    old_message_bot(api, "second", "Mailbox: ben@acme.example\n")           # two bots for one person
    old_message_bot(api, "no-mailbox", "Runs mail, says nothing of whose.\n")
    old_message_bot(api, "a-role", "Mailbox: cara@acme.example\n", template="support")
    assert backfill(api) == []
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE bots SET state='archived' WHERE slug='second'")
    assert backfill(api) == ["first"]
    # An owner who takes the link away is not overruled at the next start.
    post(api, "access/people/ben", {"inbox_bot": ""})
    assert inbox_bot_of(api, "ben") is None and backfill(api) == []
