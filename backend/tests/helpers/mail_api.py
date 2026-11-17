"""Helpers that other tests share, moved out of test_mail_api.py so they import no test file."""

from backend.auth import Identity
from backend.store import H, encode
from backend.tests.helpers.connectors import mail_message


ORG = [
    {"id": "ana", "name": "Ana", "email": "ana@acme.example", "primary_for": ["*"], "inbox_bot": "inbox"},
    {
        "id": "ben",
        "name": "Ben",
        "email": "ben@acme.example",
        "reports_to": "ana",
        "primary_for": ["cpo", "product-design"],
    },
    {"id": "lena", "name": "Lena", "email": "lena@acme.example", "reports_to": "ana"},
    {"id": "mira", "name": "Mira", "email": "mira@acme.example", "reports_to": "lena"},
    {"id": "carla", "name": "Carla", "email": "carla@acme.example", "reports_to": "lena"},
    {"id": "cara", "name": "Cara", "email": "cara@acme.example"},
]


def seed_org(api):
    api.app.state.store.settings.test_identities["lena-test"] = Identity("human:lena", "human", "lena@acme.example")
    with api.app.state.store.transaction() as c:
        H.sync_registry(c, {}, {"people": ORG})
        c.execute("INSERT OR REPLACE INTO registry_metadata VALUES('people',?)", (encode({"people": ORG}),))


def put_mail(api, mailbox, **overrides):
    """A message in the mailbox's Gmail (the `fake_gmail` fixture's): Tico reads mail live and keeps no copy."""
    from backend.tests.helpers.fake_gmail import CURRENT

    fake = CURRENT.get("fake")
    assert fake is not None, "put_mail needs the fake_gmail fixture"
    row = mail_message(**overrides)
    fake.add(
        mailbox,
        row["msg_id"],
        thread=row["thread_id"],
        sender=row["from_addr"],
        sender_header=row["from_header"] if row["from_addr"].lower() in row["from_header"].lower() else "",
        to=row["to"],
        cc=row["cc"],
        subject=row["subject"],
        body=row["body"],
        snippet=row["snippet"],
        epoch=row["epoch"],
        labels=row["labels"],
    )
    return row
