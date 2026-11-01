"""Email & Phone audience (backend/inbound.py): an inbox is the company's unless an admin narrows it to a group, and a
narrowed inbox is not there for anyone outside that group and the groups under it, on every read path: the inbox list,
messages, search, the cursor, waits, a message and its code. The owner and admins always read it.

Ana owns the company, Ben is an admin (a member after `as_member`), Cara is a member in East, under Sales."""

from backend.tests.test_api import api, as_member, get, headers, put, setup_attempt  # noqa: F401  (fixture)
from backend.tests.test_groups import roster_of, set_roster
from backend.tests.test_inbound import page, seed


def narrow(api):
    doc = roster_of(api)
    doc["org_groups"] = {**(doc.get("org_groups") or {}), "sales": {"name": "Sales"},
                         "east": {"name": "East", "parent": "sales"}, "ops-team": {"name": "Operations"}}
    for person in doc["people"]:
        if person["id"] == "cara":
            person["team"] = "east"
    set_roster(api, doc)
    svc = seed(api)
    svc.ingest(page(1, inbox="ibx_b", ident="ses:msg:ibx_b", body="Your verification code is 123456."))
    svc.ingest(page(2, inbox="ibx_a", ident="ses:msg:ibx_a", body="Hello from Acme."))
    return svc


def reads(api, token):
    """What each read path gives this reader of ibx_b, the narrowed inbox."""
    h = headers(token)
    inboxes = [i["inbox_id"] for i in api.get("/api/v2/inbound/inboxes", headers=h).json()["inboxes"]]
    listed = [m["message_id"] for m in api.get("/api/v2/inbound/messages", headers=h).json()["messages"]]
    found = [m["message_id"] for m in api.get("/api/v2/inbound/messages?q=verification", headers=h).json()["messages"]]
    waited = api.post("/api/v2/inbound/wait", headers=h, json={"after": 0, "timeout": 0}).json()["messages"]
    return {"inboxes": "ibx_b" in inboxes, "messages": "ses:msg:ibx_b" in listed, "search": "ses:msg:ibx_b" in found,
            "wait": "ses:msg:ibx_b" in [m["message_id"] for m in waited],
            "scoped": api.get("/api/v2/inbound/messages?inbox_ids=ibx_b", headers=h).status_code,
            "read": api.get("/api/v2/inbound/messages/ses:msg:ibx_b", headers=h).status_code,
            "code": api.get("/api/v2/inbound/messages/ses:msg:ibx_b/code", headers=h).status_code,
            "cursor": api.get("/api/v2/inbound/cursor", headers=h).json()["cursor"]}


OPEN = {"inboxes": True, "messages": True, "search": True, "wait": True, "scoped": 200, "read": 200, "code": 200}
SHUT = {"inboxes": False, "messages": False, "search": False, "wait": False, "scoped": 404, "read": 404, "code": 404}


def test_an_inbox_is_the_companys_until_an_admin_narrows_it(api):
    narrow(api)
    rows = {i["inbox_id"]: i for i in get(api, "inbound/inboxes")["inboxes"]}
    assert rows["ibx_b"]["audience"] == "company" and rows["ibx_b"]["group_id"] is None
    as_member(api, "ben@acme.example")
    for token in ("ana-test", "ben-test", "cara-test"):
        assert {k: v for k, v in reads(api, token).items() if k != "cursor"} == OPEN
    put(api, "inbound/inboxes/ibx_b/audience", {"audience": "team", "group_id": "sales"}, "cara-test", expected=403)
    put(api, "inbound/inboxes/ibx_b/audience", {"audience": "team", "group_id": "nowhere"}, expected=422)
    put(api, "inbound/inboxes/ibx_b/audience", {"audience": "team"}, expected=422)
    put(api, "inbound/inboxes/missing/audience", {"audience": "company"}, expected=404)
    changed = put(api, "inbound/inboxes/ibx_b/audience", {"audience": "team", "group_id": "sales"})
    assert changed["audience"] == "team" and changed["group_id"] == "sales"
    with api.app.state.store.read() as c:
        audit = c.execute("SELECT actor FROM audit_log WHERE object='inbound_inbox:ibx_b'").fetchall()
        assert [r["actor"] for r in audit] == ["human:ana"]
        assert c.execute("SELECT count(*) FROM events WHERE action='inbound.inbox_audience' AND target='ibx_b'"
                         ).fetchone()[0] == 1


def test_a_narrowed_inbox_is_gone_on_every_read_path_outside_its_group(api):
    narrow(api)
    put(api, "inbound/inboxes/ibx_b/audience", {"audience": "team", "group_id": "sales"})
    as_member(api, "ben@acme.example")                     # a member in no group
    assert {k: v for k, v in reads(api, "ana-test").items() if k != "cursor"} == OPEN     # the owner
    assert {k: v for k, v in reads(api, "cara-test").items() if k != "cursor"} == OPEN    # East is under Sales
    ben = reads(api, "ben-test")
    assert {k: v for k, v in ben.items() if k != "cursor"} == SHUT
    assert ben["cursor"] == 2 and reads(api, "ana-test")["cursor"] == 2
    # The other inbox stays the company's.
    listed = [m["message_id"] for m in get(api, "inbound/messages", "ben-test")["messages"]]
    assert listed == ["ses:msg:ibx_a"]
    # An agent outside the group is shut out the same way.
    attempt = setup_attempt(api)[2]
    assert {k: v for k, v in reads(api, attempt["token"]).items() if k != "cursor"} == SHUT
    # Back to the company: everyone reads it again.
    put(api, "inbound/inboxes/ibx_b/audience", {"audience": "company", "group_id": "sales"})
    assert {k: v for k, v in reads(api, "ben-test").items() if k != "cursor"} == OPEN


def test_the_cursor_does_not_count_a_hidden_inboxs_messages(api):
    svc = narrow(api)
    put(api, "inbound/inboxes/ibx_b/audience", {"audience": "team", "group_id": "ops-team"})
    svc.ingest(page(3, inbox="ibx_b", ident="ses:late:ibx_b", body="Another one."))
    assert reads(api, "cara-test")["cursor"] == 2
    assert reads(api, "ana-test")["cursor"] == 3


def test_sync_gaps_name_only_inboxes_the_reader_may_read(api):
    import json
    narrow(api)
    put(api, "inbound/inboxes/ibx_b/audience", {"audience": "team", "group_id": "ops-team"})
    gaps = [{"inbox_id": "ibx_a", "from": 1, "to": 2, "count": 1}, {"inbox_id": "ibx_b", "from": 3, "to": 4, "count": 2}]
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE inbound_sync SET gaps_json=? WHERE singleton=1", (json.dumps(gaps),))
    assert [g["inbox_id"] for g in get(api, "inbound/inboxes", "cara-test")["gaps"]] == ["ibx_a"]
    assert [g["inbox_id"] for g in get(api, "inbound/inboxes")["gaps"]] == ["ibx_a", "ibx_b"]


def test_who_reads_an_inbox_is_a_catalogue_setting_botops_and_the_assistant_may_card():
    from backend import assistant, settings_catalog
    path = "/api/v2/inbound/inboxes/ibx_b/audience"
    found = [e for e, _ in settings_catalog.routed("PUT", path)]
    assert [(e.key, e.kind, e.who) for e in found] == [("inbound.inbox.audience", "confirm", "admin")]
    assert assistant.valid_operation("PUT", path, None, "botops")
    assert assistant.valid_operation("PUT", path, None, "assistant")
