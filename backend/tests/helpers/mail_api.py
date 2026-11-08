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
    row = mail_message(**overrides)
    now = H.now()
    to_text = " ".join(row["to"])
    with api.app.state.store.transaction() as c:
        person = c.execute("SELECT id FROM humans WHERE lower(email)=?", (mailbox,)).fetchone()
        c.execute(
            "INSERT INTO mail_messages(mailbox,msg_id,thread_id,epoch,date,from_addr,from_header,"
            "to_addresses,cc,subject,snippet,labels,body,body_truncated,attachments,"
            "list_id,is_internal,has_unsubscribe,rule_hits,updated_at,deleted_at) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,NULL)",
            (
                mailbox,
                row["msg_id"],
                row["thread_id"],
                row["epoch"],
                row["date"],
                row["from_addr"],
                row["from_header"],
                encode(row["to"]),
                encode(row["cc"]),
                row["subject"],
                row["snippet"],
                encode(row["labels"]),
                row["body"],
                1 if row["body_truncated"] else 0,
                encode(row["attachments"]),
                row["list_id"],
                1 if row["is_internal"] else 0,
                1 if row["has_unsubscribe"] else 0,
                encode(row.get("rule_hits") or []),
                now,
            ),
        )
        c.execute("DELETE FROM mail_fts WHERE mailbox=? AND msg_id=?", (mailbox, row["msg_id"]))
        c.execute(
            "INSERT INTO mail_fts(subject,body,from_addr,to_text,mailbox,msg_id) VALUES(?,?,?,?,?,?)",
            (row["subject"], row["body"], row["from_addr"], to_text, mailbox, row["msg_id"]),
        )
        stats = c.execute(
            "SELECT count(*) AS n, min(epoch) AS oldest, max(epoch) AS newest "
            "FROM mail_messages WHERE mailbox=? AND deleted_at IS NULL",
            (mailbox,),
        ).fetchone()
        c.execute(
            "INSERT INTO mail_mailboxes(address,person_id,runner_id,synced_at,message_count,"
            "oldest_epoch,newest_epoch,error) VALUES(?,?,?,?,?,?,?,NULL) "
            "ON CONFLICT(address) DO UPDATE SET person_id=excluded.person_id,"
            "synced_at=excluded.synced_at,message_count=excluded.message_count,"
            "oldest_epoch=excluded.oldest_epoch,newest_epoch=excluded.newest_epoch,error=NULL",
            (mailbox, person["id"] if person else None, "", now, stats["n"], stats["oldest"], stats["newest"]),
        )
    return row
