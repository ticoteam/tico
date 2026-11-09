"""Stored legacy mail is browsable by people, never by bots. Live Workspace coverage is in test_mail_live."""

from backend.tests.helpers.api import get, runner, setup_attempt  # noqa: F401
from backend.tests.test_api import api  # noqa: F401
from backend.tests.helpers.mail_api import put_mail, seed_org


def seed_mail(api):
    seed_org(api)
    put_mail(
        api,
        "ana@acme.example",
        msg_id="c1",
        thread_id="tc",
        subject="Ana invoice",
        body="Please pay the invoice.",
        snippet="Please pay",
        epoch=100,
    )
    put_mail(
        api,
        "ben@acme.example",
        msg_id="s1",
        thread_id="ts",
        subject="Ben standup",
        body="Standup notes for product.",
        snippet="Standup notes",
        epoch=90,
        from_addr="eng@acme.example",
    )
    put_mail(
        api,
        "lena@acme.example",
        msg_id="l1",
        thread_id="tl",
        subject="Lena welcome",
        body="Welcome the new host.",
        snippet="Welcome the",
        epoch=80,
    )
    put_mail(
        api,
        "mira@acme.example",
        msg_id="m1",
        thread_id="tm",
        subject="Mira follow-up",
        body="Following up with the guest.",
        snippet="Following up",
        epoch=70,
    )
    put_mail(
        api,
        "carla@acme.example",
        msg_id="k1",
        thread_id="tk",
        subject="Carla review",
        body="Review the stay.",
        snippet="Review the",
        epoch=60,
    )
    put_mail(
        api,
        "cara@acme.example",
        msg_id="d1",
        thread_id="td",
        subject="Cara private",
        body="Keep this between us.",
        snippet="Keep this",
        epoch=50,
    )


def boxes(api, token="ana-test"):
    return {row["address"] for row in get(api, "mail/mailboxes", token)["mailboxes"]}


def subjects(api, token, **params):
    qs = "&".join(f"{k}={v}" for k, v in params.items() if v is not None and v != "")
    path = "mail/messages" + ("?" + qs if qs else "")
    return [row["subject"] for row in get(api, path, token)["messages"]]


def test_steven_sees_only_his_mailbox_and_bots_and_runners_see_none(api):
    seed_mail(api)
    assert boxes(api, "ben-test") == {"ben@acme.example"}
    assert subjects(api, "ben-test") == ["Ben standup"]
    get(api, "mail/messages?mailbox=ana@acme.example", "ben-test", expected=403)
    get(api, "mail/messages/c1?mailbox=ana@acme.example", "ben-test", expected=403)
    msg = get(api, "mail/messages/s1?mailbox=ben@acme.example", "ben-test")
    assert msg["body"] == "Standup notes for product." and msg["subject"] == "Ben standup"
    machine = runner(api)
    get(api, "mail/messages", machine["token"], expected=403)
    _, _, attempt = setup_attempt(api)
    get(api, "mail/mailboxes", attempt["token"], expected=403)
    get(api, "mail/messages/c1?mailbox=ana@acme.example", attempt["token"], expected=403)
