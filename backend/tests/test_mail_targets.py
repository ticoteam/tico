"""Which mailboxes the mail and calendar sync targets, and which one a message bot's token is for.

A message bot's mailbox is the address its `gmail` tool declares in bot.yaml (BotOps fills it from the
`Mailbox:` line). Only when it declares none does its person's own email stand in.
"""

import os

from backend import routines
from backend.store import encode
from backend.tests.test_api import api, get, post, runner  # noqa: F401
from backend.tests.test_mail_api import seed_org
from runner.connectors import ConnectorPublisher

DECLARED = "ana@acme.team"


def declare(api, identity):
    """Give the `inbox` bot (Ana's message bot) a gmail tool with this identity, in bot.yaml's shape."""
    tools = [{"service": "gmail", "identity": identity, "can": ["read", "draft"]}]
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE bot_config SET config_json=? WHERE bot='inbox'",
                  (encode({"name": "inbox", "runtime": "fake", "status": "active", "tools": tools}),))


def mail_targets(api, token=None):
    return get(api, "connectors/mail/targets", token or runner(api)["token"])["mailboxes"]


def test_targets_are_the_message_bots_declared_mailboxes_not_every_person(api):
    seed_org(api)
    declare(api, DECLARED)
    boxes = mail_targets(api)
    assert [b["address"] for b in boxes] == [DECLARED]          # Ben, Lena, Mira, Carla and Cara have no message bot
    assert boxes[0]["person_id"] == "ana"
    # The message bot's token covers the declared mailbox and the people below; nobody else's bot gets any.
    with api.app.state.store.read() as c:
        assert routines.token_mailboxes(c, "inbox") == [
            DECLARED, "ben@acme.example", "lena@acme.example", "mira@acme.example", "carla@acme.example"]
        assert routines.token_mailboxes(c, "coo") == []


# The runner: a domain the key can't act for is reported once, not failed on every cycle.

class Hub:
    def __init__(self, boxes):
        self.boxes, self.reports = boxes, []

    def get(self, path):
        assert path == "connectors/mail/targets"
        return {"mailboxes": [{"address": a, "message_count": 1} for a in self.boxes], "batch": 100}

    def post(self, path, body):
        assert path == "connectors/health"
        self.reports.append(body)
        return {"ok": True}


def publisher(tmp_path, hub, mail):
    script = tmp_path / "hub/scripts/mail.sh"
    script.parent.mkdir(parents=True, exist_ok=True)
    script.write_text("#!/bin/sh\nexit 1\n")
    script.chmod(os.stat(script).st_mode | 0o100)
    made = ConnectorPublisher({"url": "http://localhost:8765", "token": "t", "projects_dir": str(tmp_path),
                               "mail_sync_seconds": 0, "owner_handle": "ana"}, client=hub, mail=mail)
    made.script = script
    return made


class Flaky:
    """A mail CLI whose one mailbox answers unauthorized_client while `refuse` is set."""
    def __init__(self, refuse=True):
        self.refuse, self.calls = refuse, 0

    def __call__(self, args):
        self.calls += 1
        if self.refuse:
            raise RuntimeError("Local mail lookup failed: unauthorized_client: Client is unauthorized")
        return {"messages": []}


def one_domain(tmp_path):
    mail, hub, now = Flaky(), Hub(["ana@acme-signin.example"]), [1000.0]
    work = publisher(tmp_path, hub, mail)
    work.clock = lambda: now[0]
    return work, mail, hub, now


def delegation_reported(hub):
    return {"account": "ana@acme-signin.example", "reason": "delegation"} in hub.reports[-1]["failing"]


def test_three_consecutive_errors_block_report_and_log_once(tmp_path, capsys):
    work, mail, hub, now = one_domain(tmp_path)
    work.mail_tick()
    work.mail_tick()
    assert not work.blocked("ana@acme-signin.example") and not delegation_reported(hub)
    work.mail_tick()                                # the third in a row, three cycles
    assert work.blocked("ana@acme-signin.example") and delegation_reported(hub)
    work.mail_tick()
    work.mail_tick()                                # blocked: not tried, still reported
    assert mail.calls == 3 and delegation_reported(hub)
    assert capsys.readouterr().out.count("cannot act for the domain acme-signin.example") == 1


def test_one_success_resets_the_domain_and_clears_the_health_issue(tmp_path, capsys):
    work, mail, hub, now = one_domain(tmp_path)
    for _ in range(3):
        work.mail_tick()
    assert delegation_reported(hub)
    now[0] += 121
    mail.refuse = False                             # the delegation was granted after all
    work.mail_tick()
    assert work.domains == {} and not work.blocked("ana@acme-signin.example")
    assert hub.reports[-1]["failing"] == []
    assert "can act for the domain acme-signin.example again" in capsys.readouterr().out
    mail.refuse = True                              # a later error starts counting from zero
    work.mail_tick()
    assert not work.blocked("ana@acme-signin.example") and hub.reports[-1]["failing"] == []


def named(api, person, body, token="ana-test", expected=200):
    return post(api, f"access/people/{person}", body, token=token, expected=expected)


def test_only_an_owner_or_admin_names_a_message_bot_and_one_bot_serves_one_person(api):
    seed_org(api)
    named(api, "ben", {"inbox_bot": "coo", "mailbox": "ben@acme.team"}, token="cara-test", expected=403)   # a member cannot
    named(api, "ben", {"inbox_bot": "inbox"}, expected=409)                # Ana's message bot
    named(api, "ben", {"mailbox": "ben@acme.team"}, expected=422)          # an address needs the bot that reads it
    named(api, "ben", {"inbox_bot": "no-such-bot"}, expected=404)
    named(api, "ben", {"inbox_bot": "coo", "mailbox": "not an address"}, expected=422)
    with api.app.state.store.read() as c:
        assert routines.token_mailboxes(c, "coo") == []
