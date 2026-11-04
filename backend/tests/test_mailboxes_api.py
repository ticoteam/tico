"""Settings > Mailboxes (backend/mailboxes.py): the admin's table of every mailbox and a member's own and shared ones;
Connect mailbox (a person's address is theirs, any other is a shared inbox or waits for its person); Make shared inbox;
who can use one mailbox (Read / Read and write), granted and revoked by its owner, or an admin for a shared inbox, and
nobody else; Stop using in Tico. Every write is a person acting as themselves: the Assistant, a bot and a task actor are
refused, and the Assistant's turn never admits these routes (backend/assistant.py NEVER).

The `mailboxes` and `mailbox_accesses` tables are made here, as the contract stub backend/mailbox_access.py expects,
until track A's migration 0082_mailboxes.py lands (then the `tables` fixture's DDL goes)."""
import pytest

from backend import mailboxes, openapi_v2
from backend.assistant import admits_on_turn
from backend.auth import Identity
from backend.tests.test_api import api, get, headers, post  # noqa: F401  (the api fixture)
from backend.store import Problem
from backend.tests.test_openapi_v2 import conforms

DDL = (
    "CREATE TABLE IF NOT EXISTS mailboxes(id TEXT PRIMARY KEY, address TEXT NOT NULL UNIQUE, provider TEXT NOT NULL, "
    "person_id TEXT, kind TEXT NOT NULL, connection TEXT NOT NULL, status TEXT NOT NULL, shared_by TEXT, shared_at TEXT, "
    "created_by TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL)",
    "CREATE TABLE IF NOT EXISTS mailbox_accesses(id TEXT PRIMARY KEY, mailbox_id TEXT NOT NULL, grantee_type TEXT NOT "
    "NULL, grantee_id TEXT NOT NULL, level TEXT NOT NULL, granted_by TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT "
    "NULL, UNIQUE(mailbox_id, grantee_type, grantee_id))",
)


@pytest.fixture
def mb(api):
    with api.app.state.store.transaction() as c:
        for statement in DDL:
            c.execute(statement)
    ids = api.app.state.store.settings.test_identities
    ids["ana-assistant"] = Identity("human:ana", "owner", "ana@acme.example", via="assistant")
    ids["cara-assistant"] = Identity("human:cara", "human", "cara@acme.example", via="assistant")
    return api


def connect(api, address, shared=False, token="ana-test", expected=200):
    return post(api, "mailboxes", {"address": address, "shared": shared}, token=token, expected=expected)


def call(api, method, path, token="ana-test", body=None):
    return api.request(method, "/api/v2/" + path, json=body, headers=headers(token))


def grant(api, mid, grantee, level="read", token="ana-test", kind="person", expected=200):
    return post(api, f"mailboxes/{mid}/accesses", {"grantee_type": kind, "grantee_id": grantee, "level": level},
                token=token, expected=expected)


def access(detail, grantee):
    return next(a for a in detail["accesses"] if a["grantee_id"] == grantee)


def events(api, action):
    with api.app.state.store.read() as c:
        return c.execute("SELECT count(*) FROM events WHERE action=?", (action,)).fetchone()[0]


def test_connect_reads_the_address_as_a_person_s_own_a_shared_inbox_or_one_awaiting_its_person(mb):
    cara = connect(mb, " Cara@Acme.example ")["mailbox"]
    assert (cara["address"], cara["kind"], cara["person_id"]) == ("cara@acme.example", "personal", "cara")
    assert cara["person_name"]
    assert cara["status"] == "active" and cara["state"] == "not_connected" and cara["routines"] == []
    support = connect(mb, "support@acme.example", shared=True)["mailbox"]
    assert (support["kind"], support["person_id"], support["state"]) == ("shared", None, "not_connected")
    # A person's address stays theirs even when ticked shared.
    assert connect(mb, "ben@acme.example", shared=True)["mailbox"]["kind"] == "personal"
    dan = connect(mb, "dan@acme.example")["mailbox"]
    assert (dan["kind"], dan["person_id"], dan["state"]) == ("personal", None, "awaiting_person")
    assert "already in Tico" in connect(mb, "cara@acme.example", expected=409)["error"]["detail"]
    connect(mb, "not an address", expected=422)
    connect(mb, "eve@acme.example", token="cara-test", expected=403)          # not an admin
    connect(mb, "eve@acme.example", token="ana-assistant", expected=403)      # nobody acting for the admin
    assert events(mb, "mailbox.connect") == 4


def test_an_admin_sees_every_mailbox_and_a_member_their_own_and_what_is_shared_with_them(mb):
    cara = connect(mb, "cara@acme.example")["mailbox"]
    support = connect(mb, "support@acme.example", shared=True)["mailbox"]
    connect(mb, "ben@acme.example")
    admin = get(mb, "mailboxes")
    assert admin["admin"] is True
    assert [m["address"] for m in admin["mailboxes"]] == ["ben@acme.example", "cara@acme.example", "support@acme.example"]
    assert admin["may"] == {"read": True, "organize": True, "send": True, "change_calendar": True}
    assert [m["address"] for m in admin["shared"]] == ["support@acme.example"]    # admins hold every shared inbox
    member = get(mb, "mailboxes", "cara-test")
    assert member["admin"] is False and member["mailboxes"] == []
    assert member["mine"]["id"] == cara["id"] and member["mine"]["level"] == "read_write"
    assert member["shared"] == []
    grant(mb, support["id"], "cara")
    shared = get(mb, "mailboxes", "cara-test")["shared"]
    assert [(m["address"], m["level"]) for m in shared] == [("support@acme.example", "read")]
    # A person's Assistant sees what the person sees.
    assert get(mb, "mailboxes", "cara-assistant")["shared"] == shared


def test_the_owner_or_an_admin_on_a_shared_inbox_grants_changes_and_revokes_and_nobody_else(mb):
    cara = connect(mb, "cara@acme.example")["mailbox"]["id"]
    support = connect(mb, "support@acme.example", shared=True)["mailbox"]["id"]
    # Cara's own mailbox: only Cara changes who can use it, not even the owner of the company.
    grant(mb, cara, "ben", token="ana-test", expected=403)
    seen = grant(mb, cara, "ben", level="read", token="cara-test")
    assert [(a["grantee_id"], a["level"], a["implicit"]) for a in seen["accesses"]] == [
        ("cara", "read_write", True), ("ben", "read", False)]
    assert "already have access" in grant(mb, cara, "ben", token="cara-test", expected=409)["error"]["detail"]
    grant(mb, cara, "cara", token="cara-test", expected=409)                    # her own: already Read and write
    grant(mb, cara, "nobody", token="cara-test", expected=404)
    grant(mb, cara, "no-such-bot", kind="bot", token="cara-test", expected=404)
    # Ben has Read: he can't reshare, change or remove.
    grant(mb, cara, "ana", token="ben-test", expected=403)
    aid = access(seen, "ben")["id"]
    assert call(mb, "PATCH", f"mailboxes/{cara}/accesses/{aid}", "ben-test", {"level": "read_write"}).status_code == 403
    assert call(mb, "DELETE", f"mailboxes/{cara}/accesses/{aid}", "ben-test").status_code == 403
    r = call(mb, "PATCH", f"mailboxes/{cara}/accesses/{aid}", "cara-test", {"level": "read_write"})
    assert r.status_code == 200, r.text
    assert access(r.json(), "ben")["level"] == "read_write"
    assert call(mb, "PATCH", f"mailboxes/{cara}/accesses/{aid}", "cara-test", {"level": "admin"}).status_code == 422
    assert call(mb, "PATCH", f"mailboxes/{cara}/accesses/nope", "cara-test", {"level": "read"}).status_code == 404
    r = call(mb, "DELETE", f"mailboxes/{cara}/accesses/{aid}", "cara-test")
    assert r.status_code == 200 and [a["grantee_id"] for a in r.json()["accesses"]] == ["cara"]
    assert call(mb, "DELETE", f"mailboxes/{cara}/accesses/{aid}", "cara-test").status_code == 404
    # A shared inbox: any admin (Ben is a bot admin) manages it; a bot gets access too.
    bot = grant(mb, support, "ops", kind="bot", level="read_write", token="ben-test")
    assert [(a["grantee_type"], a["name"], a["implicit"]) for a in bot["accesses"]] == [
        ("admins", "Admins", True), ("bot", "ops", False)]
    grant(mb, support, "cara", token="cara-test", expected=403)
    assert (events(mb, "mailbox.access.grant"), events(mb, "mailbox.access.change"),
            events(mb, "mailbox.access.revoke")) == (2, 1, 1)


def test_nobody_acting_for_a_person_changes_who_can_use_a_mailbox(mb):
    cara = connect(mb, "cara@acme.example")["mailbox"]["id"]
    support = connect(mb, "support@acme.example", shared=True)["mailbox"]["id"]
    grant(mb, cara, "ben", token="cara-assistant", expected=403)
    grant(mb, support, "cara", token="ana-assistant", expected=403)
    # A bot (its credential is a runner's lease, so the route checks the identity itself), and a task actor.
    for who in (Identity("bot:ops", "bot"), Identity("human:ana", "owner", "ana@acme.example", task_actor="bot:ops")):
        with pytest.raises(Problem) as refused:
            mailboxes._person(who)
        assert refused.value.status == 403
    assert call(mb, "POST", f"mailboxes/{support}/share", "ana-assistant").status_code == 403
    assert call(mb, "DELETE", f"mailboxes/{cara}", "cara-assistant").status_code == 403
    # And the Assistant's own turn never admits them.
    for method, path in (("POST", "/api/v2/mailboxes"), ("POST", f"/api/v2/mailboxes/{cara}/accesses"),
                         ("PATCH", f"/api/v2/mailboxes/{cara}/accesses/a1"),
                         ("DELETE", f"/api/v2/mailboxes/{cara}/accesses/a1"),
                         ("POST", f"/api/v2/mailboxes/{cara}/share"), ("DELETE", f"/api/v2/mailboxes/{cara}")):
        assert not admits_on_turn(method, path, {}), (method, path)
    # Their read: what the person may see, and no controls.
    seen = get(mb, f"mailboxes/{cara}", "cara-assistant")
    assert seen["mailbox"]["level"] == "read_write"
    assert seen["can_manage"] is False and seen["can_stop"] is False and seen["candidates"] == []


def test_one_mailbox_reads_only_for_who_can_use_it_and_admins_and_lists_its_routines(mb):
    ana = connect(mb, "ana@acme.example")["mailbox"]["id"]
    cara = connect(mb, "cara@acme.example")["mailbox"]["id"]
    aid = post(mb, "automations", {"name": "Triage", "owner": "person:ana",
                                   "trigger": {"type": "message", "inbox": "ana@acme.example"},
                                   "steps": [{"id": "tell", "type": "action", "do": "wake_bot",
                                              "args": {"bot": "ops", "text": "{{message.subject}}"}}]})["id"]
    mine = get(mb, f"mailboxes/{ana}")
    assert mine["routines"] == [{"id": aid, "name": "Triage", "enabled": True, "can_edit": True}]
    assert mine["mailbox"]["routines"] == [{"id": aid, "name": "Triage"}]
    assert mine["can_manage"] is True and mine["can_stop"] is True
    assert any(x["type"] == "person" and x["id"] == "ben" for x in mine["candidates"])
    assert not any(x["type"] == "person" and x["id"] == "ana" for x in mine["candidates"])
    assert any(x["type"] == "bot" and x["id"] == "ops" for x in mine["candidates"])
    assert [m["routines"] for m in get(mb, "mailboxes")["mailboxes"] if m["id"] == ana] == [[{"id": aid, "name": "Triage"}]]
    # Cara has no access to Ana's: it isn't there for her.
    get(mb, f"mailboxes/{ana}", "cara-test", expected=404)
    get(mb, "mailboxes/nope", expected=404)
    # The admin reads Cara's mailbox but doesn't manage it; she may stop Tico using it.
    theirs = get(mb, f"mailboxes/{cara}")
    assert theirs["can_manage"] is False and theirs["candidates"] == [] and theirs["can_stop"] is True
    assert theirs["mailbox"]["level"] == "none"


def test_make_shared_and_stop(mb):
    dan = connect(mb, "dan@acme.example")["mailbox"]["id"]
    cara = connect(mb, "cara@acme.example")["mailbox"]["id"]
    assert call(mb, "POST", f"mailboxes/{dan}/share", "cara-test").status_code == 403
    assert call(mb, "POST", f"mailboxes/{cara}/share").status_code == 409            # a person's own
    r = call(mb, "POST", f"mailboxes/{dan}/share")
    assert r.status_code == 200, r.text
    assert r.json()["mailbox"]["kind"] == "shared" and r.json()["can_manage"] is True
    # Stop: the owner or an admin; anyone else is refused.
    assert call(mb, "DELETE", f"mailboxes/{cara}", "ben-test").status_code == 200    # Ben is an admin
    connect(mb, "cara@acme.example")                                                  # back in use
    assert call(mb, "DELETE", f"mailboxes/{dan}", "cara-test").status_code == 403
    r = call(mb, "DELETE", f"mailboxes/{cara}", "cara-test")
    assert r.status_code == 200 and r.json()["mailbox"]["status"] == "stopped" and r.json()["mailbox"]["state"] == "stopped"
    assert get(mb, "mailboxes", "cara-test")["mine"] is None
    assert events(mb, "mailbox.stop") == 2 and events(mb, "mailbox.share") == 1


def test_the_declared_answers_match_the_live_ones(mb):
    document = mb.get("/api/v2/openapi.json", headers=headers()).json()
    by_id = {op["operationId"]: op for ops in document["paths"].values() for op in ops.values()}

    def check(operation, response):
        assert response.status_code == 200, response.text
        schema = by_id[operation]["responses"]["200"]["content"]["application/json"]["schema"]
        assert conforms(response.json(), schema, document) is None, (operation, conforms(response.json(), schema, document))
        return response.json()

    mid = check("connectMailbox", call(mb, "POST", "mailboxes", body={"address": "support@acme.example", "shared": True}))[
        "mailbox"]["id"]
    dan = check("connectMailbox", call(mb, "POST", "mailboxes", body={"address": "dan@acme.example"}))["mailbox"]["id"]
    check("listMailboxes", call(mb, "GET", "mailboxes"))
    check("listMailboxes", call(mb, "GET", "mailboxes", "cara-test"))
    check("shareMailbox", call(mb, "POST", f"mailboxes/{dan}/share"))
    added = check("addMailboxAccess", call(mb, "POST", f"mailboxes/{mid}/accesses",
                                           body={"grantee_type": "person", "grantee_id": "cara", "level": "read"}))
    aid = access(added, "cara")["id"]
    check("getMailbox", call(mb, "GET", f"mailboxes/{mid}"))
    check("changeMailboxAccess", call(mb, "PATCH", f"mailboxes/{mid}/accesses/{aid}", body={"level": "read_write"}))
    check("removeMailboxAccess", call(mb, "DELETE", f"mailboxes/{mid}/accesses/{aid}"))
    check("stopMailbox", call(mb, "DELETE", f"mailboxes/{mid}"))
    assert {"listMailboxes", "connectMailbox", "getMailbox", "stopMailbox", "shareMailbox", "addMailboxAccess",
            "changeMailboxAccess", "removeMailboxAccess"} <= set(by_id)
    assert "Mailboxes" in openapi_v2.TAGS
