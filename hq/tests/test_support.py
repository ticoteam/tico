"""HQ support tickets: strict input, per-ticket secrets, staff-only routes, replies the install can fetch, messages from
the person, no body in a log, and no automatic deletion."""

import json
import logging
import uuid
from datetime import datetime, timedelta, timezone

import httpx
import pytest
from fastapi.testclient import TestClient

from hq.app import create_app
from hq.db import Database
from hq.limits import Limiter
from hq.releases import Latest
from hq.support import Tickets

STAFF = "s" * 32
NOW = datetime(2026, 11, 1, 12, 0, 0, tzinfo=timezone.utc)
SECRET_TEXT = "my-launch-plan-is-confidential"


@pytest.fixture
def moment():
    return [NOW]


@pytest.fixture
def ticks():
    return [0.0]


@pytest.fixture
def db(tmp_path):
    return Database(tmp_path / "hq.db")


@pytest.fixture
def tickets(db, moment, ticks):
    return Tickets(db, now=lambda: moment[0], clock=lambda: ticks[0])


@pytest.fixture
def client(db, tickets):
    latest = Latest(transport=httpx.MockTransport(lambda request: httpx.Response(200, json={"tag_name": "v0.2.17"})))
    with TestClient(create_app(db, latest, staff_key=STAFF, tickets=tickets), client=("203.0.113.9", 5000)) as c:
        yield c


def file(client, message="The board will not load", **extra):
    return client.post("/v1/support", json={"message": message, **extra})


def staff(key=STAFF):
    return {"Authorization": "Bearer " + key}


def test_a_ticket_is_stored_and_the_secret_only_as_a_hash(client, db):
    install = str(uuid.uuid4())
    r = file(client, SECRET_TEXT, email="ana@acme.example", install_id=install, version="0.2.17", app_version="0.3.47")
    assert r.status_code == 201
    body = r.json()
    assert body["status"] == "open" and body["ticket_id"].startswith("TK-") and len(body["secret"]) >= 30
    row = dict(db.conn.execute("SELECT * FROM tickets").fetchone())
    assert row["body"] == SECRET_TEXT and row["email"] == "ana@acme.example" and row["install_id"] == install
    assert row["version"] == "0.2.17" and row["app_version"] == "0.3.47"
    assert file(client, app_version="0.3.47; rm").status_code == 422
    assert row["created"] == "2026-11-01T12:00:00Z" and row["status"] == "open"
    assert body["secret"] not in "".join(db.conn.iterdump()) and row["key_hash"] != body["secret"]
    assert "203.0.113.9" not in "".join(db.conn.iterdump())      # no address, ever


@pytest.mark.parametrize("bad", [{"message": "a\x00b"}, {"email": "not-an-email"}, {"extra": "field"}])
def test_bad_input_is_refused_and_nothing_is_stored(client, db, bad):
    body = {"message": "hello", **bad}
    if bad.get("message") is None and "message" in bad:
        body = {"message": None}
    r = client.post("/v1/support", json=body)
    assert r.status_code == 422 and r.json()["error"] == "invalid"
    assert "hello" not in r.text                                 # the refusal names the field, never echoes a value
    assert db.conn.execute("SELECT count(*) FROM tickets").fetchone()[0] == 0


def test_the_limits_of_size_and_shape(client, db):
    assert file(client, "x" * 4000).status_code == 201
    assert client.post("/v1/support", content=b"{" + b" " * 20000 + b"}").status_code == 413
    assert client.post("/v1/support", content=b"not json").status_code == 422
    assert client.post("/v1/support", content=b"[1,2]").status_code == 422
    assert client.post("/v1/support", content=b"\xff\xfe").status_code == 422
    assert db.conn.execute("SELECT count(*) FROM tickets").fetchone()[0] == 1


def test_five_tickets_an_hour_per_address_and_ten_a_day_per_install(client, tickets, ticks):
    assert [file(client).status_code for _ in range(6)] == [201] * 5 + [429]
    ticks[0] += 3601
    assert file(client).status_code == 201
    install = str(uuid.uuid4())
    for _ in range(10):                      # an hour apart, so only the install's own daily limit can stop it
        ticks[0] += 3601
        assert file(client, install_id=install).status_code == 201
    ticks[0] += 3601
    assert file(client, install_id=install).status_code == 429
    assert file(client).status_code == 201   # another install from the same address is not held by that one
    assert all("203.0.113" not in str(k) for limiter in (tickets.per_address, tickets.per_install)
               for k in limiter.hits)


def test_the_person_reads_status_and_replies_with_the_right_secret_only(client):
    made = file(client).json()
    url = "/v1/support/" + made["ticket_id"]
    ok = client.get(url, headers={"X-Ticket-Secret": made["secret"]})
    assert ok.status_code == 200 and ok.json()["status"] == "open" and ok.json()["messages"] == []
    assert set(ok.json()) == {"ticket_id", "status", "created", "updated", "messages"}    # no email or install id
    assert client.get(url + "?secret=" + made["secret"]).status_code == 200
    other = file(client).json()
    for wrong in ({"X-Ticket-Secret": "w" * 32}, {"X-Ticket-Secret": other["secret"]}, {"X-Ticket-Secret": "short"}, {}):
        assert client.get(url, headers=wrong).status_code == 404
    assert client.get("/v1/support/TK-AAAAAAAA", headers={"X-Ticket-Secret": made["secret"]}).status_code == 404
    assert client.get("/v1/support/not-a-ticket", headers={"X-Ticket-Secret": made["secret"]}).status_code == 404


def test_staff_routes_need_the_staff_key(client, db):
    made = file(client).json()
    routes = [("get", "/v1/staff/tickets", None), ("get", "/v1/staff/tickets/" + made["ticket_id"], None),
              ("post", "/v1/staff/tickets/%s/reply" % made["ticket_id"], {"body": "hi"}),
              ("post", "/v1/staff/tickets/%s/status" % made["ticket_id"], {"status": "closed"})]
    for method, path, body in routes:
        for headers in ({}, staff("x" * 32), staff(""), {"Authorization": STAFF}, {"X-Ticket-Secret": made["secret"]}):
            assert getattr(client, method)(path, headers=headers, **({"json": body} if body else {})).status_code == 401
    assert db.conn.execute("SELECT count(*) FROM ticket_replies").fetchone()[0] == 0
    assert db.conn.execute("SELECT status FROM tickets").fetchone()[0] == "open"
    assert client.get("/v1/staff/tickets", headers=staff()).status_code == 200


def test_staff_stats_show_exact_counts_per_release_and_the_public_ones_stay_suppressed(client, db):
    for version in ("0.2.24", "0.2.24", "0.2.9"):
        db.record(str(uuid.uuid4()), version, True, False)
    assert client.get("/v1/staff/stats").status_code == 401
    assert client.get("/v1/staff/stats", headers=staff("x" * 32)).status_code == 401
    got = client.get("/v1/staff/stats", headers=staff())
    assert got.status_code == 200 and got.headers["cache-control"] == "no-store"
    assert got.json() == {"by_version": {"0.2.9": 1, "0.2.24": 2}, "window_days": 7}
    assert client.get("/v1/stats").json()["by_version"] == {}


def test_without_a_staff_key_the_staff_routes_do_not_exist(db, tickets):
    latest = Latest(transport=httpx.MockTransport(lambda request: httpx.Response(200, json={"tag_name": "v0.2.17"})))
    with TestClient(create_app(db, latest, tickets=tickets)) as c:
        assert c.get("/v1/staff/tickets", headers=staff("")).status_code == 404
        assert c.get("/v1/staff/tickets", headers=staff(STAFF)).status_code == 404


def test_repeated_wrong_keys_are_slowed(client):
    codes = [client.get("/v1/staff/tickets", headers=staff("z" * 32)).status_code for _ in range(22)]
    assert codes[:20] == [401] * 20 and codes[20:] == [429, 429]
    assert client.get("/v1/staff/tickets", headers=staff()).status_code == 200      # the right key still works


def test_staff_list_filters_and_a_reply_reaches_the_install(client, moment):
    first = file(client, "first", email="ana@acme.example").json()
    moment[0] += timedelta(minutes=10)
    second = file(client, "second").json()
    listed = client.get("/v1/staff/tickets?status=open", headers=staff()).json()["tickets"]
    assert [t["body"] for t in listed] == ["first", "second"]
    assert listed[0]["email"] == "ana@acme.example" and listed[0]["email_pending"] is False
    since = client.get("/v1/staff/tickets?since=2026-11-01T12:05:00Z", headers=staff()).json()["tickets"]
    assert [t["ticket_id"] for t in since] == [second["ticket_id"]]

    moment[0] += timedelta(minutes=5)
    reply = client.post("/v1/staff/tickets/%s/reply" % first["ticket_id"], headers=staff(),
                        json={"body": "Try the latest release.\n<b>not html</b>"})
    assert reply.status_code == 200
    assert reply.json() == {"ticket_id": first["ticket_id"], "status": "answered", "reply_id": 1, "email_pending": True}
    assert client.get("/v1/staff/tickets?status=open", headers=staff()).json()["tickets"][0]["ticket_id"] == second["ticket_id"]
    shown = client.get("/v1/staff/tickets/" + first["ticket_id"], headers=staff()).json()
    assert shown["status"] == "answered" and shown["email_pending"] is True and len(shown["messages"]) == 1

    seen = client.get("/v1/support/" + first["ticket_id"], headers={"X-Ticket-Secret": first["secret"]}).json()
    assert seen["status"] == "answered" and seen["messages"][0]["body"] == "Try the latest release.\n<b>not html</b>"
    assert seen["messages"][0]["created"] == "2026-11-01T12:15:00Z" and seen["messages"][0]["from"] == "staff"
    # A ticket with no email address is never "email pending".
    quiet = client.post("/v1/staff/tickets/%s/reply" % second["ticket_id"], headers=staff(), json={"body": "ok"})
    assert quiet.json()["email_pending"] is False

    done = client.post("/v1/staff/tickets/%s/status" % first["ticket_id"], headers=staff(),
                       json={"status": "closed", "email_sent": True})
    assert done.json() == {"ticket_id": first["ticket_id"], "status": "closed", "email_pending": False}
    assert client.post("/v1/staff/tickets/%s/reply" % first["ticket_id"], headers=staff(), json={"body": "late"}).status_code == 409


@pytest.mark.parametrize("body", [{"body": "x" * 8001}])
def test_staff_input_is_checked_too(client, body):
    made = file(client).json()
    assert client.post("/v1/staff/tickets/%s/reply" % made["ticket_id"], headers=staff(), json=body).status_code == 422
    for bad in ({}, {"status": "deleted"}, {"status": "closed", "x": 1}, {"email_sent": "yes"}):
        assert client.post("/v1/staff/tickets/%s/status" % made["ticket_id"], headers=staff(), json=bad).status_code == 422
    assert client.post("/v1/staff/tickets/TK-AAAAAAAA/reply", headers=staff(), json={"body": "x"}).status_code == 404
    assert client.get("/v1/staff/tickets?status=weird", headers=staff()).status_code == 422
    assert client.get("/v1/staff/tickets?since=yesterday", headers=staff()).status_code == 422


def test_no_ticket_text_or_address_reaches_a_log(client, caplog, tmp_path):
    with caplog.at_level(logging.DEBUG):
        made = file(client, SECRET_TEXT, email="ana@acme.example").json()
        client.get("/v1/support/" + made["ticket_id"], headers={"X-Ticket-Secret": made["secret"]})
        client.post("/v1/staff/tickets/%s/reply" % made["ticket_id"], headers=staff(), json={"body": "reply-text-xyz"})
        client.post("/v1/support", content=b"{" + SECRET_TEXT.encode())
    logged = " ".join(r.getMessage() for r in caplog.records if not r.name.startswith("httpx"))
    for needle in (SECRET_TEXT, "ana@acme.example", "reply-text-xyz", made["secret"], STAFF, "203.0.113.9"):
        assert needle not in logged


def test_the_person_can_write_again_until_it_is_closed_and_the_team_sees_it(client, moment):
    made = file(client, "first", email="ana@acme.example").json()
    url, mine = "/v1/support/" + made["ticket_id"], {"X-Ticket-Secret": made["secret"]}
    client.post("/v1/staff/tickets/%s/reply" % made["ticket_id"], headers=staff(), json={"body": "Did you restart?"})
    moment[0] += timedelta(minutes=3)
    r = client.post(url + "/messages", headers=mine, json={"message": "Yes, and it still fails."})
    assert r.status_code == 201 and r.json()["status"] == "open"
    seen = client.get(url, headers=mine).json()
    assert seen["status"] == "open" and [(m["from"], m["body"]) for m in seen["messages"]] == [
        ("staff", "Did you restart?"), ("person", "Yes, and it still fails.")]
    # The team's list shows it again, later than before, so a poller with a cursor sees the new message.
    again = client.get("/v1/staff/tickets?since=2026-11-01T12:00:30Z", headers=staff()).json()["tickets"]
    assert again[0]["ticket_id"] == made["ticket_id"] and again[0]["messages"][1]["from"] == "person"
    for bad in ({}, {"message": ""}, {"message": "x" * 4001}, {"message": "a", "extra": 1}, {"message": "\x00"}):
        assert client.post(url + "/messages", headers=mine, json=bad).status_code == 422
    assert client.post(url + "/messages", headers={"X-Ticket-Secret": "w" * 32}, json={"message": "hi"}).status_code == 404
    assert client.post(url + "/messages", json={"message": "hi"}).status_code == 404
    client.post("/v1/staff/tickets/%s/status" % made["ticket_id"], headers=staff(), json={"status": "closed"})
    assert client.post(url + "/messages", headers=mine, json={"message": "one more"}).status_code == 409


def test_nothing_deletes_a_ticket_by_itself_and_the_person_or_the_team_can(db, client, moment, tmp_path):
    old = file(client, "old").json()
    client.post("/v1/staff/tickets/%s/reply" % old["ticket_id"], headers=staff(), json={"body": "answer"})
    client.post("/v1/staff/tickets/%s/status" % old["ticket_id"], headers=staff(), json={"status": "closed"})
    moment[0] += timedelta(days=800)                    # long past 90 days, and past 13 months
    assert db.purge() == 0                              # the install rows' 13 months do not touch tickets
    assert len(client.get("/v1/staff/tickets?status=closed", headers=staff()).json()["tickets"]) == 1
    assert client.get("/v1/support/" + old["ticket_id"], headers={"X-Ticket-Secret": old["secret"]}).status_code == 200

    mine, theirs = file(client, SECRET_TEXT).json(), file(client, "someone else").json()
    url = "/v1/support/" + mine["ticket_id"]
    assert client.delete(url, headers={"X-Ticket-Secret": theirs["secret"]}).status_code == 404     # not with another's secret
    assert client.delete(url).status_code == 404
    assert client.delete(url, headers={"X-Ticket-Secret": mine["secret"]}).json() == {"deleted": True}
    assert client.get(url, headers={"X-Ticket-Secret": mine["secret"]}).status_code == 404
    assert not any(SECRET_TEXT.encode() in f.read_bytes() for f in tmp_path.glob("hq.db*"))    # overwritten, not unlinked

    assert client.delete("/v1/staff/tickets/" + theirs["ticket_id"]).status_code == 401
    assert client.delete("/v1/staff/tickets/" + theirs["ticket_id"], headers=staff()).json() == {"deleted": True}
    assert client.delete("/v1/staff/tickets/" + theirs["ticket_id"], headers=staff()).status_code == 404
    assert db.conn.execute("SELECT count(*) FROM tickets").fetchone()[0] == 1
    assert db.conn.execute("SELECT count(*) FROM ticket_replies WHERE ticket_id IN (?,?)",
                           (mine["ticket_id"], theirs["ticket_id"])).fetchone()[0] == 0


def test_the_existing_request_limit_does_not_apply_to_polling_and_still_holds_elsewhere(db, tickets):
    latest = Latest(transport=httpx.MockTransport(lambda request: httpx.Response(200, json={"tag_name": "v0.2.17"})))
    limiter = Limiter(limit=2, window=3600, clock=lambda: 0.0)
    with TestClient(create_app(db, latest, limiter, staff_key=STAFF, tickets=tickets)) as c:
        assert [c.get("/v1/stats").status_code for _ in range(3)] == [200, 200, 429]
        assert [c.get("/v1/support/TK-AAAAAAAA").status_code for _ in range(4)] == [404] * 4


# ---------------------------------------------------------------------- diagnostics
BUNDLE = {"format": 1, "versions": {"tico": "0.2.18"}, "counts": {"bots": 3}, "health": [{"name": "Computers", "status": "ok"}],
          "logs": {"server": ["2026-11-01T11:59:00Z WARNING tico.x: Sync for person-1 failed"], "updater": []}}


def test_diagnostics_are_kept_with_the_ticket_and_only_staff_read_them(client, db):
    made = file(client, "It will not load", diagnostics=BUNDLE)
    assert made.status_code == 201
    tid, mine = made.json()["ticket_id"], {"X-Ticket-Secret": made.json()["secret"]}
    assert json.loads(db.conn.execute("SELECT diagnostics FROM tickets").fetchone()[0]) == BUNDLE
    # The person's own route never returns it, and neither does a staff listing (it says there is one).
    assert "diagnostics" not in client.get(f"/v1/support/{tid}", headers=mine).text
    assert "diagnostics" not in client.get(f"/v1/support/{tid}", headers=mine).json()
    listed = client.get("/v1/staff/tickets", headers=staff()).json()["tickets"][0]
    assert listed["has_diagnostics"] is True and "diagnostics" not in listed
    assert client.get(f"/v1/staff/tickets/{tid}", headers=staff()).json()["diagnostics"] == BUNDLE
    # Nobody else reads it: not without a key, not with the ticket's own secret, not with a wrong key.
    for headers in ({}, mine, staff("x" * 32)):
        assert client.get(f"/v1/staff/tickets/{tid}", headers=headers).status_code == 401
    # A ticket without them says so, and deleting the ticket deletes them.
    plain = file(client, "Another").json()["ticket_id"]
    shown = client.get(f"/v1/staff/tickets/{plain}", headers=staff()).json()
    assert shown["has_diagnostics"] is False and "diagnostics" not in shown
    assert client.delete(f"/v1/staff/tickets/{tid}", headers=staff()).status_code == 200
    assert "0.2.18" not in "".join(db.conn.iterdump())


def test_diagnostics_are_checked_for_size_and_shape(client, db, ticks):
    limit = 256 * 1024
    over = {**BUNDLE, "logs": {"server": ["x" * 1000] * 262}}          # over the bundle cap, inside the request's
    assert file(client, diagnostics=over).status_code == 422
    assert file(client, diagnostics={**BUNDLE, "logs": {"server": ["x" * 1000] * 300}}).status_code == 413
    fits = {**BUNDLE, "logs": {"server": ["x" * 1000] * 200}}          # under it, and the request is bigger than a ticket's
    assert 16 * 1024 < len(json.dumps(fits)) < limit and file(client, diagnostics=fits).status_code == 201
    for bad in ("text", [1], {"versions": {}}, {"format": 2}, {**BUNDLE, "x": "a\x00b"}, {**BUNDLE, "x": {"a": {"b": {"c": {"d": {"e": {"f": {"g": 1}}}}}}}}):
        ticks[0] += 3601                                               # the per-address hourly limit is not what is tested
        assert file(client, diagnostics=bad).status_code == 422
    # Only the diagnostics may make a request bigger than a message needs.
    ticks[0] += 3601
    assert client.post("/v1/support", json={"message": "hi", "note": "y" * 20000}).status_code in (413, 422)
    ticks[0] += 3601
    assert client.post("/v1/support", content=b"{" + b" " * 20000 + b"}").status_code == 413
    assert db.conn.execute("SELECT count(*) FROM tickets").fetchone()[0] == 1


def test_an_older_database_gets_the_column_once_and_starting_again_changes_nothing(tmp_path):
    old = Database(tmp_path / "old.db")
    old.conn.executescript("""CREATE TABLE tickets(ticket_id TEXT PRIMARY KEY, key_hash TEXT NOT NULL, created TEXT NOT NULL,
        updated TEXT NOT NULL, closed TEXT, status TEXT NOT NULL DEFAULT 'open', body TEXT NOT NULL, email TEXT, install_id TEXT,
        version TEXT, email_pending INTEGER NOT NULL DEFAULT 0);
        INSERT INTO tickets(ticket_id,key_hash,created,updated,body) VALUES('TK-AAAAAAAA','h','t','t','kept');""")
    for _ in range(2):
        Tickets(old)
        assert [r[1] for r in old.conn.execute("PRAGMA table_info(tickets)")].count("diagnostics") == 1
    assert old.conn.execute("SELECT body, diagnostics FROM tickets").fetchone()[:] == ("kept", None)


def test_followup_diagnostics_stay_with_the_message_and_delete_with_the_ticket(client, db):
    made = file(client, diagnostics=BUNDLE).json()
    tid = made["ticket_id"]
    mine = {"X-Ticket-Secret": made["secret"]}
    followup = {"format": 1, "created": "2026-10-02T12:00:00Z", "logs": {"server": ["recovered"]}}
    r = client.post(f"/v1/support/{tid}/messages", headers=mine, json={"message": "After retry", "diagnostics": followup})
    assert r.status_code == 201, r.text
    visible = client.get(f"/v1/support/{tid}", headers=mine).json()["messages"][0]
    assert visible["has_diagnostics"] and "diagnostics" not in visible
    full = client.get(f"/v1/staff/tickets/{tid}", headers=staff()).json()
    assert full["diagnostics"] == BUNDLE and full["messages"][0]["diagnostics"] == followup
    assert client.post(f"/v1/support/{tid}/messages", headers=mine, json={"message": "Bad", "diagnostics": {"format": 999}}).status_code == 422
    assert client.delete(f"/v1/support/{tid}", headers=mine).status_code == 200
    assert db.conn.execute("SELECT count(*) FROM ticket_replies WHERE ticket_id=?", (tid,)).fetchone()[0] == 0


# ---------------------------------------------------------------------- Health Monitor signatures
SIG = "hm-a1b2c3d4e5f6"
OTHER = "hm-000000000000"
HM_BODY = ("[Health Monitor] queue_idle: queued work does not start\n\nSignature: %s\nKind: tico-bug\nTico: 0.3.46\n"
           "First seen: 2026-11-01T11:00 UTC; seen 3 time(s) in 2 place(s).")


def test_a_health_monitor_signature_is_kept_and_the_staff_can_group_by_it(client):
    first = file(client, HM_BODY % SIG).json()["ticket_id"]
    second = file(client, HM_BODY % OTHER).json()["ticket_id"]
    third = file(client, "A person's own words").json()["ticket_id"]
    listed = {t["ticket_id"]: t for t in client.get("/v1/staff/tickets?status=all", headers=staff()).json()["tickets"]}
    assert (listed[first]["signature"], listed[second]["signature"], listed[third]["signature"]) == (SIG, OTHER, "")
    assert client.get("/v1/staff/tickets/" + first, headers=staff()).json()["signature"] == SIG
    grouped = client.get("/v1/staff/tickets?status=all&signature=" + SIG, headers=staff()).json()["tickets"]
    assert [t["ticket_id"] for t in grouped] == [first]
    assert client.get("/v1/staff/tickets?signature=nope", headers=staff()).status_code == 422


def test_a_release_closes_every_ticket_with_a_signature_and_replies_to_each(client):
    one = file(client, HM_BODY % SIG, email="ana@acme.example").json()
    two = file(client, HM_BODY % SIG).json()
    file(client, HM_BODY % OTHER)
    shut = file(client, HM_BODY % SIG).json()["ticket_id"]
    client.post("/v1/staff/tickets/%s/status" % shut, headers=staff(), json={"status": "closed"})
    fixed = "Fixed in Tico 0.3.47: queued work starts while its computer has room. Update to 0.3.47."
    done = client.post("/v1/staff/tickets/by-signature/close", headers=staff(),
                       json={"signature": SIG, "body": fixed})
    assert done.status_code == 200
    assert done.json() == {"signature": SIG,
                           "closed": [{"ticket_id": one["ticket_id"], "reply_id": 1, "email_pending": True},
                                      {"ticket_id": two["ticket_id"], "reply_id": 2, "email_pending": False}],
                           "already_closed": 1}
    for made in (one, two):
        seen = client.get("/v1/support/" + made["ticket_id"], headers={"X-Ticket-Secret": made["secret"]}).json()
        assert seen["status"] == "closed" and seen["messages"][0]["body"] == fixed
    still_open = client.get("/v1/staff/tickets?status=open", headers=staff()).json()["tickets"]
    assert len(still_open) == 1 and still_open[0]["signature"] == OTHER
    open_left = client.get("/v1/staff/tickets?status=open&signature=" + OTHER, headers=staff()).json()["tickets"]
    assert len(open_left) == 1
    # Running it again closes nothing new.
    again = client.post("/v1/staff/tickets/by-signature/close", headers=staff(),
                        json={"signature": SIG, "body": fixed}).json()
    assert again == {"signature": SIG, "closed": [], "already_closed": 3}
    for bad in ({"signature": SIG}, {"signature": SIG, "body": fixed, "x": 1}, {"signature": "hm-xyz", "body": fixed},
                {"signature": SIG, "body": ""}, {"signature": SIG, "body": "x" * 8001}):
        assert client.post("/v1/staff/tickets/by-signature/close", headers=staff(), json=bad).status_code == 422
    assert client.post("/v1/staff/tickets/by-signature/close", json={"signature": SIG, "body": fixed}).status_code == 401


def test_an_older_database_backfills_signatures_once(tmp_path):
    old = Database(tmp_path / "old.db")
    old.conn.executescript("""CREATE TABLE tickets(ticket_id TEXT PRIMARY KEY, key_hash TEXT NOT NULL, created TEXT NOT NULL,
        updated TEXT NOT NULL, closed TEXT, status TEXT NOT NULL DEFAULT 'open', body TEXT NOT NULL, email TEXT, install_id TEXT,
        version TEXT, email_pending INTEGER NOT NULL DEFAULT 0);
        INSERT INTO tickets(ticket_id,key_hash,created,updated,body) VALUES('TK-AAAAAAAA','h','t','t','%s');""" % (HM_BODY % SIG))
    for _ in range(2):
        Tickets(old)
        assert old.conn.execute("SELECT signature FROM tickets").fetchone()[0] == SIG


def test_a_setup_or_feedback_request_keeps_its_kind_and_an_unknown_kind_is_refused(client, db):
    setup = file(client, "Want help setting up", kind="setup").json()["ticket_id"]
    feedback = file(client, "Working well", kind="feedback").json()["ticket_id"]
    plain = file(client).json()["ticket_id"]
    listed = {t["ticket_id"]: t["kind"] for t in client.get("/v1/staff/tickets?status=all", headers=staff()).json()["tickets"]}
    assert listed == {setup: "setup", feedback: "feedback", plain: "support"}
    count = db.conn.execute("SELECT count(*) FROM tickets").fetchone()[0]
    assert file(client, kind="billing").json() == {"error": "invalid", "field": "kind"}
    assert db.conn.execute("SELECT count(*) FROM tickets").fetchone()[0] == count
