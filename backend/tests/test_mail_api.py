"""Server-stored mail is browsable by people, never by bots, and never live from Gmail."""


from backend.auth import Identity
from backend.store import H, encode
from backend.tests.test_api import api, get, runner, setup_attempt  # noqa: F401
from backend.tests.test_connectors import mail_message


ORG = [
    {"id": "ana", "name": "Ana", "email": "ana@acme.example", "primary_for": ["*"],
     "inbox_bot": "inbox"},
    {"id": "ben", "name": "Ben", "email": "ben@acme.example", "reports_to": "ana",
     "primary_for": ["cpo", "product-design"]},
    {"id": "lena", "name": "Lena", "email": "lena@acme.example", "reports_to": "ana"},
    {"id": "mira", "name": "Mira", "email": "mira@acme.example", "reports_to": "lena"},
    {"id": "carla", "name": "Carla", "email": "carla@acme.example", "reports_to": "lena"},
    {"id": "cara", "name": "Cara", "email": "cara@acme.example"},
]


def seed_org(api):
    api.app.state.store.settings.test_identities["lena-test"] = Identity(
        "human:lena", "human", "lena@acme.example")
    with api.app.state.store.transaction() as c:
        H.sync_registry(c, {}, {"people": ORG})
        c.execute("INSERT OR REPLACE INTO registry_metadata VALUES('people',?)",
                  (encode({"people": ORG}),))


def put_mail(api, mailbox, **overrides):
    row = mail_message(**overrides)
    now = H.now()
    to_text = " ".join(row["to"])
    with api.app.state.store.transaction() as c:
        person = c.execute("SELECT id FROM humans WHERE lower(email)=?", (mailbox,)).fetchone()
        c.execute(
            "INSERT INTO mail_messages(mailbox,msg_id,thread_id,epoch,date,from_addr,from_header,"
            "to_json,cc_json,subject,snippet,labels_json,body,body_truncated,attachments_json,"
            "list_id,is_internal,has_unsubscribe,rule_hits_json,updated,deleted_at) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,NULL)",
            (mailbox, row["msg_id"], row["thread_id"], row["epoch"], row["date"],
             row["from_addr"], row["from_header"], encode(row["to"]), encode(row["cc"]),
             row["subject"], row["snippet"], encode(row["labels"]), row["body"],
             1 if row["body_truncated"] else 0, encode(row["attachments"]), row["list_id"],
             1 if row["is_internal"] else 0, 1 if row["has_unsubscribe"] else 0,
             encode(row.get("rule_hits") or []), now))
        c.execute("DELETE FROM mail_fts WHERE mailbox=? AND msg_id=?", (mailbox, row["msg_id"]))
        c.execute("INSERT INTO mail_fts(subject,body,from_addr,to_text,mailbox,msg_id) VALUES(?,?,?,?,?,?)",
                  (row["subject"], row["body"], row["from_addr"], to_text, mailbox, row["msg_id"]))
        stats = c.execute("SELECT count(*) AS n, min(epoch) AS oldest, max(epoch) AS newest "
                          "FROM mail_messages WHERE mailbox=? AND deleted_at IS NULL",
                          (mailbox,)).fetchone()
        c.execute("INSERT INTO mail_mailboxes(address,person_id,runner_id,synced_at,message_count,"
                  "oldest_epoch,newest_epoch,error) VALUES(?,?,?,?,?,?,?,NULL) "
                  "ON CONFLICT(address) DO UPDATE SET person_id=excluded.person_id,"
                  "synced_at=excluded.synced_at,message_count=excluded.message_count,"
                  "oldest_epoch=excluded.oldest_epoch,newest_epoch=excluded.newest_epoch,error=NULL",
                  (mailbox, person["id"] if person else None, "", now,
                   stats["n"], stats["oldest"], stats["newest"]))
    return row


def seed_mail(api):
    seed_org(api)
    put_mail(api, "ana@acme.example", msg_id="c1", thread_id="tc", subject="Ana invoice",
             body="Please pay the invoice.", snippet="Please pay", epoch=100)
    put_mail(api, "ben@acme.example", msg_id="s1", thread_id="ts", subject="Ben standup",
             body="Standup notes for product.", snippet="Standup notes", epoch=90,
             from_addr="eng@acme.example")
    put_mail(api, "lena@acme.example", msg_id="l1", thread_id="tl", subject="Lena welcome",
             body="Welcome the new host.", snippet="Welcome the", epoch=80)
    put_mail(api, "mira@acme.example", msg_id="m1", thread_id="tm", subject="Mira follow-up",
             body="Following up with the guest.", snippet="Following up", epoch=70)
    put_mail(api, "carla@acme.example", msg_id="k1", thread_id="tk", subject="Carla review",
             body="Review the stay.", snippet="Review the", epoch=60)
    put_mail(api, "cara@acme.example", msg_id="d1", thread_id="td", subject="Cara private",
             body="Keep this between us.", snippet="Keep this", epoch=50)


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
