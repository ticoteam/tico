"""Shared threads between two installs over a fake Tico HQ relay (backend/federation*.py). Two real apps, each with its own
database, Gateway key and install id, run the real core threads module. One in-memory HQ keeps the relay's own rules:
a peering needs both approvals and a pinned key, a pause on either side stops it, an envelope needs a live signature,
and a repeat is refused unless the test asks HQ to keep one. Each install's relay is ticked by hand, on one fake clock."""
import base64
import contextlib
import hashlib
import json
import time
import uuid
from dataclasses import dataclass

import httpx
import pytest
import yaml
from fastapi.testclient import TestClient

from backend import federation as F
from backend import federation_envelope as E
from backend import federation_relay as R
from backend import federation_sync as S
from backend import federation_threads as T
from backend import gateway_connection as GC
from backend import hook_relay
from backend import hubdb as H
from backend import org_rule_check as OR
from backend import screening
from backend import thread_digest as DIGEST
from backend import threads as CORE
from backend.app import create_app
from backend.auth import Identity
from backend.config import Settings
from backend.store import Problem, encode
from backend.tests.test_org_rule_check import COLOR, Judge

OWNER = {"Authorization": "Bearer ana-test"}
BEN = {"Authorization": "Bearer ben-test"}
PEOPLE = {"people": [{"id": "ana", "email": "ana@acme.example", "primary_for": ["*"]},
                     {"id": "ben", "email": "ben@acme.example"}]}
URL = "https://updates.tico.team/v1/federation/envelopes"


class HQ:
    """Tico HQ's federation relay, in memory. `lose(sender, to, body)` drops an envelope after HQ accepts it; `twice`
    lets HQ keep an envelope it already has (a replay)."""

    def __init__(self, clock):
        self.clock, self.by_key, self.keys, self.peerings, self.mail = clock, {}, {}, {}, {}
        self.seen, self.hq_seq = set(), 0
        self.lose = lambda sender, to, body: False
        self.twice = False

    def __call__(self, request):
        path = request.url.path.split("/federation/", 1)[1]
        me = self.by_key.get(request.headers.get("authorization", "").removeprefix("Bearer "))
        if me is None:
            return httpx.Response(401, json={"error": "unauthorized"})
        if path == "envelopes":
            return self.envelope(me, request)
        status, answer = self.route(me, request.method, path, json.loads(request.content or b"{}"))
        return httpx.Response(status, json=answer)

    def route(self, me, method, path, body):
        if path == "keys" and method == "POST":
            self.keys[me] = {"key_id": E.key_id_of(base64.b64decode(body["public_key"])), "public_key": body["public_key"]}
            return 201, {"install": me, **self.keys[me]}
        if path.startswith("keys/"):
            found = self.keys.get(path[len("keys/"):])
            return (200, found) if found else (404, {"error": "not_found"})
        if path == "peerings" and method == "GET":
            return 200, {"items": [self.view(pid, me) for pid, p in self.peerings.items() if me in (p["a"], p["b"])]}
        if path == "peerings" and method == "POST":
            peer = body["peer"]
            if peer not in self.keys:
                return 422, {"error": "unknown_install"}
            if any({p["a"], p["b"]} == {me, peer} and not p["revoked"] for p in self.peerings.values()):
                return 409, {"error": "already_peered"}
            pid = "fp_" + uuid.uuid4().hex[:24]
            self.peerings[pid] = {"id": pid, "a": me, "b": peer, "requester": me, "approved": {}, "paused": {},
                                  "revoked": False, "company": {me: str(body.get("company") or "")}}
            return 201, self.view(pid, me)
        parts = path.split("/")
        if len(parts) == 3 and parts[0] == "peerings":
            p = self.peerings.get(parts[1])
            if p is None or me not in (p["a"], p["b"]):
                return 404, {"error": "not_found"}
            other = p["b"] if me == p["a"] else p["a"]
            if parts[2] == "approve":
                if body.get("peer_key_id") != self.keys[other]["key_id"]:
                    return 422, {"error": "key_changed"}
                p["approved"][me] = body["peer_key_id"]
                p["company"][me] = str(body.get("company") or "")
            elif parts[2] == "pause":
                p["paused"][me] = bool(body["paused"])
            elif parts[2] == "revoke":
                p["revoked"] = True
            else:
                return 404, {"error": "not_found"}
            return 200, self.view(p["id"], me)
        if path == "pull":
            cursor = int(body.get("cursor") or 0)
            items = [i for i in self.mail.get(me, []) if i["hq_seq"] > cursor][:int(body.get("limit") or 50)]
            return 200, {"items": items, "cursor": items[-1]["hq_seq"] if items else cursor, "more": False}
        if path == "ack":
            through = int(body["through_hq_seq"])
            self.mail[me] = [i for i in self.mail.get(me, []) if i["hq_seq"] > through]
            return 200, {"acked": through}
        return 404, {"error": "not_found"}

    def view(self, pid, me):
        p = self.peerings[pid]
        other = p["b"] if me == p["a"] else p["a"]
        status = "revoked" if p["revoked"] else "active" if len(p["approved"]) == 2 else "pending"
        return {"id": pid, "peer": other, "status": status, "requested_by_you": p["requester"] == me,
                "you_approved": me in p["approved"], "peer_approved": other in p["approved"],
                "you_paused": p["paused"].get(me, False), "peer_paused": p["paused"].get(other, False),
                "your_key_id": self.keys[me]["key_id"], "peer_key_id": self.keys[other]["key_id"], "peer_company": p["company"].get(other, ""), "updated": ""}

    def live(self, a, b):
        for p in self.peerings.values():
            if {p["a"], p["b"]} == {a, b} and not p["revoked"] and len(p["approved"]) == 2:
                return p
        return None

    def envelope(self, me, request):
        headers, body = request.headers, bytes(request.content or b"")
        to, env, sent = headers.get("x-tico-to", ""), headers.get("x-tico-envelope", ""), headers.get("x-tico-sent-at", "0")
        if len(body) > 256 * 1024:
            return httpx.Response(413, json={"error": "too_large"})
        p = self.live(me, to)
        if p is None:
            return httpx.Response(403, json={"error": "no_peering"})
        if p["paused"].get(me) or p["paused"].get(to):
            return httpx.Response(403, json={"error": "paused"})
        key = self.keys[me]
        if headers.get("x-tico-key-id") != key["key_id"]:
            return httpx.Response(403, json={"error": "key_mismatch"})
        if abs(int(self.clock()) - int(sent)) > E.SKEW:
            return httpx.Response(403, json={"error": "stale"})
        sig = headers.get("x-tico-signature", "")
        if not E.verify(base64.b64decode(key["public_key"]), sig, E.signed_text(me, to, env, int(sent), body)):
            return httpx.Response(403, json={"error": "bad_signature"})
        if (me, env) in self.seen and not self.twice:
            return httpx.Response(409, json={"error": "duplicate"})
        self.seen.add((me, env))
        if self.lose(me, to, body):
            return httpx.Response(201, json={"accepted": True})
        self.hq_seq += 1
        item = {"hq_seq": self.hq_seq, "from": me, "envelope_id": env, "peering_id": p["id"],
                "received_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(self.clock())),
                "headers": {"x-tico-key-id": key["key_id"], "x-tico-sent-at": sent, "x-tico-signature": sig,
                            "x-tico-envelope": env, "x-tico-to": to},
                "body_b64": base64.b64encode(body).decode()}
        self.mail.setdefault(to, []).append(item)
        return httpx.Response(201, json={"accepted": True})


@dataclass
class Side:
    app: object
    client: TestClient
    id: str
    key: str


@pytest.fixture
def clock(monkeypatch):
    now = {"t": 1_800_000_000.0}
    monkeypatch.setattr(R, "now", lambda: now["t"])
    return now


@pytest.fixture
def hq(monkeypatch, clock):
    relay = HQ(lambda: clock["t"])
    monkeypatch.setattr(hook_relay, "TRANSPORT", httpx.MockTransport(relay))
    return relay


@pytest.fixture
def make_side(tmp_path, hq):
    stack = contextlib.ExitStack()

    def make(name, admins=()):
        root = tmp_path / name
        registry = root / "registry"
        registry.mkdir(parents=True)
        (registry / "hub-access.yaml").write_text(yaml.safe_dump({"owner": "ana@acme.example", "bot_admins": list(admins)}))
        settings = Settings(db_path=root / "hub.db", registry_dir=registry, gateway_key="tgw_" + name, company_name=name.title() + " Co", test_identities={
            "ana-test": Identity("human:ana", "owner", "ana@acme.example"),
            "ben-test": Identity("human:ben", "human", "ben@acme.example"),
            "bot-test": Identity("bot:ops", "bot", agent="hermes")})
        app = create_app(settings)
        client = stack.enter_context(TestClient(app))          # the lifespan creates the schema
        install = "gi_" + hashlib.sha256(name.encode()).hexdigest()[:24]
        with app.state.store.transaction() as c:
            GC._write(c, {"install": install})
            H.sync_registry(c, {}, PEOPLE)                    # the humans rows sign-in reads
            c.execute("INSERT INTO bots(slug,display_name,state) VALUES('ops','Ops','active')")
            c.execute("INSERT INTO agents(bot,harness,token_hash,created,created_by) VALUES('ops','hermes','x',?,'human:ana')",
                      (H.now(),))
            c.execute("INSERT INTO registry_metadata VALUES('people',?) ON CONFLICT(key) "
                      "DO UPDATE SET value_json=excluded.value_json", (encode(PEOPLE),))
        hq.by_key["tgw_" + name] = install
        return Side(app, client, install, "tgw_" + name)

    yield make
    stack.close()


# ----------------------------------------------------------------------------- helpers
def tick(side):
    relay = side.app.state.federation_relay
    relay.synced, relay.next_try = 0.0, 0.0            # every tick reads HQ's peerings, so a change shows at once
    relay.tick()
    with side.app.state.store.read() as c:
        error = c.execute("SELECT last_error FROM federation_state WHERE singleton=1").fetchone()[0]
    assert error == "", error


def held_reasons(side):
    """Why each envelope this install holds (in arrival order): a check that has not run, a paused peering, or behind one."""
    return [r[0] for r in rows(side, "SELECT reason FROM federation_deferrals ORDER BY rowid")]


def drive(*sides, rounds=6):
    for _ in range(rounds):
        for side in sides:
            tick(side)


def status(side):
    return side.client.get("/api/v2/federation", headers=OWNER).json()


def row_for(side, peer_id):
    return next(p["id"] for p in status(side)["peerings"] if p["peer"] == peer_id)


def connect(a, b, hq):
    """Both installs register keys; A asks B; B approves; A approves; both are active after their next look at HQ."""
    for side in (a, b):
        assert side.client.post("/api/v2/federation/keys", headers=OWNER).status_code == 200
    asked = a.client.post("/api/v2/federation/peerings", json={"peer": b.id},
                          headers={**OWNER, "Idempotency-Key": "ask-" + b.id})
    assert asked.status_code == 200, asked.text
    tick(b)
    assert b.client.post(f"/api/v2/federation/peerings/{row_for(b, a.id)}/approve", headers=OWNER).status_code == 200
    tick(a)
    assert a.client.post(f"/api/v2/federation/peerings/{row_for(a, b.id)}/approve", headers=OWNER).status_code == 200
    drive(a, b, rounds=2)
    assert status(a)["peerings"][0]["status"] == "active" and status(b)["peerings"][0]["status"] == "active"


def make_thread(side, title):
    """A members thread the owner is in: a hidden one could not be shared by them."""
    with side.app.state.store.transaction() as c:
        tid = CORE.create(c, "keeper", title=title, audience="members")["id"]
        CORE.add_member(c, "keeper", tid, "human:ana")
    return tid


def share(home, tid, other):
    response = home.client.post(f"/api/v2/federation/threads/{tid}/installs", json={"install": other.id}, headers=OWNER)
    assert response.status_code == 200, response.text


def say(side, tid, text, who="human:ana"):
    with side.app.state.store.transaction() as c:
        return CORE.post(c, who, tid, text)["id"]


def edit(side, message_id, text, who="human:ana"):
    with side.app.state.store.transaction() as c:
        CORE.edit_message(c, who, message_id, text)


def local_thread(side, home_thread, home):
    with side.app.state.store.read() as c:
        return c.execute("SELECT thread_id FROM federation_threads WHERE origin_id=? AND home_install=?",
                         (home_thread, home.id)).fetchone()[0]


def reader(side, thread, actor):
    with side.app.state.store.transaction() as c:
        CORE.add_member(c, "keeper", thread, actor)


def texts(side, thread):
    with side.app.state.store.read() as c:
        return [r[0] for r in c.execute("SELECT body FROM messages WHERE conversation_id=? AND deleted_at IS NULL "
                                        "ORDER BY seq", (thread,))]


def outcomes(side, peer, envelope_id):
    with side.app.state.store.read() as c:
        return [r[0] for r in c.execute("SELECT outcome FROM federation_receipts WHERE peer_install=? AND envelope_id=? "
                                        "ORDER BY received_at", (peer.id, envelope_id))]


def sign_and_post(hq, sender, to, body, envelope_id=None, sent=None):
    """An envelope from `sender` to `to`, signed with the sender's own key, sent to HQ as the sender."""
    with sender.app.state.store.read() as c:
        key = F.key(c)
        private = F.private(c, sender.app.state.vault, key)
    envelope_id = envelope_id or E.new_envelope_id()
    sent = int(sent if sent is not None else hq.clock())
    signature = E.sign(private, sender.id, to.id, envelope_id, sent, body)
    headers = {"Authorization": "Bearer " + sender.key, "x-tico-to": to.id, "x-tico-envelope": envelope_id,
               "x-tico-sent-at": str(sent), "x-tico-key-id": key["key_id"], "x-tico-signature": signature}
    with httpx.Client(transport=httpx.MockTransport(hq)) as http:
        response = http.post(URL, headers=headers, content=body)
    assert response.status_code == 201, response.text
    return envelope_id


def forged(home_thread, actor, origin, revision, text="", deleted=False):
    """A message payload with any actor, origin and revision: what a hostile peer would send."""
    return E.payload_bytes({"type": "message", "thread": home_thread, "origin_id": origin, "revision": revision,
                            "actor": actor, "message_kind": "say", "body": text, "deleted": deleted, "created": ""})


def message_body(home_thread, actor, text, origin=None, revision=0):
    return E.payload_bytes({"type": "message", "thread": home_thread, "origin_id": origin or "msg-" + uuid.uuid4().hex,
                            "revision": revision, "actor": actor, "message_kind": "say", "body": text, "deleted": False,
                            "created": ""})


# ----------------------------------------------------------------------------- the tests
def test_peering_needs_both_approvals_and_both_sides_see_it_active(make_side, hq):
    a, b = make_side("alpha"), make_side("beta")
    connect(a, b, hq)
    assert hq.peerings[next(iter(hq.peerings))]["approved"] and len(hq.peerings) == 1


def test_peering_request_needs_an_idempotency_key(make_side, hq):
    a, b = make_side("alpha"), make_side("beta")
    a.client.post("/api/v2/federation/keys", headers=OWNER)
    refused = a.client.post("/api/v2/federation/peerings", json={"peer": b.id}, headers=OWNER)
    assert refused.status_code == 422


def test_the_owner_and_an_admin_manage_federation_and_nobody_else(make_side, hq):
    plain, admin = make_side("alpha"), make_side("beta", admins=["ben@acme.example"])
    assert plain.client.get("/api/v2/federation", headers=BEN).status_code == 403
    assert plain.client.post("/api/v2/federation/keys", headers=BEN).status_code == 403
    assert admin.client.get("/api/v2/federation", headers=BEN).status_code == 200
    assert admin.client.post("/api/v2/federation/keys", headers=BEN).status_code == 200


def test_a_bot_or_a_non_owner_botops_run_is_refused_every_federation_management_write(make_side, hq):
    """BotOps may delegate these rows, so the route is the check: a bot, and a person BotOps acts for (ben), get 403."""
    a = make_side("alpha")
    writes = [("POST", "/api/v2/federation/keys", None), ("POST", "/api/v2/federation/keys/rotate", None),
              ("POST", "/api/v2/federation/peerings", {"peer": "gi_other"}),
              ("POST", "/api/v2/federation/peerings/p1/approve", None),
              ("POST", "/api/v2/federation/peerings/p1/pause", {"paused": True}),
              ("POST", "/api/v2/federation/peerings/p1/revoke", None),
              ("POST", "/api/v2/federation/threads/t1/installs", {"install": "gi_other"}),
              ("POST", "/api/v2/federation/threads/t1/readers", {"actor": "human:ben"}),
              ("DELETE", "/api/v2/federation/threads/t1/installs/gi_other", None)]
    for token in ("bot-test", "ben-test"):
        headers = {"Authorization": "Bearer " + token}
        for method, path, body in writes:
            got = a.client.request(method, path, json=body, headers=headers)
            assert got.status_code == 403, (token, method, path, got.status_code)


def test_a_shared_thread_reaches_the_other_install_in_both_directions(make_side, hq):
    a, b = make_side("alpha"), make_side("beta")
    connect(a, b, hq)
    tid = make_thread(a, "Launch")
    share(a, tid, b)
    say(a, tid, "hello from alpha")
    drive(a, b)
    there = local_thread(b, tid, a)
    assert texts(b, there) == ["hello from alpha"]
    say(b, there, "hi from beta", who="human:ben")
    drive(a, b)
    assert texts(a, tid) == ["hello from alpha", "hi from beta"]
    assert texts(b, there) == ["hello from alpha", "hi from beta"]


def test_an_edit_that_arrives_before_its_message_waits_for_a_catch_up(make_side, hq):
    a, b = make_side("alpha"), make_side("beta")
    connect(a, b, hq)
    tid = make_thread(a, "Launch")
    share(a, tid, b)
    drive(a, b)
    lost = {"done": False}

    def lose_first_message(sender, to, body):
        if sender == a.id and to == b.id and not lost["done"] and json.loads(body).get("home_seq") == 2:
            lost["done"] = True
            return True
        return False

    hq.lose = lose_first_message
    message = say(a, tid, "first")
    drive(a, b, rounds=1)
    edit(a, message, "first, edited")
    drive(a, b)
    there = local_thread(b, tid, a)
    assert lost["done"]
    assert texts(b, there) == ["first, edited"]                  # the first numbered copy came back on the catch-up
    assert rows(b, "SELECT m.revision FROM messages m WHERE m.conversation_id=?", (there,)) == [(1,)]
    with b.app.state.store.read() as c:
        assert c.execute("SELECT catch_up_requested_at FROM federation_threads WHERE thread_id=?",
                         (there,)).fetchone()[0] is not None


def lose_messages_to(hq, sender, to):
    """HQ loses every message item from `sender` to `to` until the switch this returns is turned off."""
    lost = {"on": True}
    hq.lose = lambda frm, recipient, body: (lost["on"] and frm == sender.id and recipient == to.id
                                            and json.loads(body).get("item_type") == "message")
    return lost


def test_a_replica_missing_more_than_a_page_gets_all_of_it_in_order_with_a_create_past_the_page(make_side, hq):
    a, b = make_side("alpha"), make_side("beta")
    connect(a, b, hq)
    tid = make_thread(a, "Launch")
    share(a, tid, b)
    drive(a, b)
    lost = lose_messages_to(hq, a, b)
    ids = [say(a, tid, f"note {n}") for n in range(S.CATCH_UP_BATCH + 40)]
    edit(a, ids[S.CATCH_UP_BATCH + 20], "past the page, edited")      # a create and its edit, both past one page
    drive(a, rounds=3)
    assert texts(b, local_thread(b, tid, a)) == []
    lost["on"] = False
    say(a, tid, "back")                                               # the gap shows, and the catch-up pages to the end
    drive(a, b, rounds=8)
    there = local_thread(b, tid, a)
    want = [f"note {n}" for n in range(S.CATCH_UP_BATCH + 40)]
    want[S.CATCH_UP_BATCH + 20] = "past the page, edited"
    assert texts(b, there) == [*want, "back"]
    with a.app.state.store.read() as c:
        assert c.execute("SELECT catch_up_after FROM federation_members WHERE thread_id=?", (tid,)).fetchone()[0] is None
    with b.app.state.store.read() as c:
        row = c.execute("SELECT applied_through,high_seq FROM federation_threads WHERE thread_id=?", (there,)).fetchone()
    assert row[0] == row[1]


def test_paging_holds_back_while_the_last_page_is_waiting_to_leave(make_side, hq, clock):
    a, b = make_side("alpha"), make_side("beta")
    connect(a, b, hq)
    tid = make_thread(a, "Launch")
    share(a, tid, b)
    drive(a, b)
    lost = lose_messages_to(hq, a, b)
    [say(a, tid, f"note {n}") for n in range(S.CATCH_UP_BATCH + 5)]
    drive(a, rounds=3)
    lost["on"] = False
    with a.app.state.store.transaction() as c:
        thread = S.thread_local(c, tid)
        S.on_catch_up(c, a.id, b.id, {"thread": thread["origin_id"], "after": 0})
        waiting = c.execute("SELECT COUNT(*) FROM federation_outbox WHERE recipient=? AND kind='item'", (b.id,)).fetchone()[0]
        S.continue_catch_ups(c)
        assert waiting == S.CATCH_UP_BATCH
        assert c.execute("SELECT COUNT(*) FROM federation_outbox WHERE recipient=? AND kind='item'",
                         (b.id,)).fetchone()[0] == waiting
        assert c.execute("SELECT catch_up_after FROM federation_members WHERE thread_id=?", (tid,)).fetchone()[0] is not None
    drive(a, b, rounds=6)
    assert len(texts(b, local_thread(b, tid, a))) == S.CATCH_UP_BATCH + 5


def test_a_removed_member_is_paged_up_to_its_removal_and_no_further(make_side, hq):
    a, b = make_side("alpha"), make_side("beta")
    connect(a, b, hq)
    tid = make_thread(a, "Launch")
    share(a, tid, b)
    drive(a, b)
    lost = lose_messages_to(hq, a, b)
    say(a, tid, "before")
    drive(a, rounds=2)
    lost["on"] = False
    assert a.client.delete(f"/api/v2/federation/threads/{tid}/installs/{b.id}", headers=OWNER).status_code == 200
    say(a, tid, "after")
    drive(a, b)
    assert texts(b, local_thread(b, tid, a)) == ["before"]


def test_a_gap_closes_without_a_later_message_once_the_throttle_lets_it_ask_again(make_side, hq, clock):
    a, b = make_side("alpha"), make_side("beta")
    connect(a, b, hq)
    tid = make_thread(a, "Launch")
    share(a, tid, b)
    drive(a, b)
    lost = lose_messages_to(hq, a, b)
    say(a, tid, "one")
    say(a, tid, "two")
    drive(a, rounds=2)
    lost["on"] = False
    say(a, tid, "three")
    tick(a)
    tick(b)                                      # "three" shows the gap and the replica asks
    lost["on"] = True
    drive(a, b, rounds=3)                        # what the home sends back is lost too: nothing more will arrive by itself
    there = local_thread(b, tid, a)
    assert texts(b, there) == []
    lost["on"] = False
    drive(a, b, rounds=3)
    assert texts(b, there) == []                 # the throttle holds the next request back
    clock["t"] += S.CATCH_UP_EVERY + 1
    drive(a, b, rounds=4)
    assert texts(b, there) == ["one", "two", "three"]


def test_a_quiet_replica_asks_once_a_day_for_a_last_message_that_never_reached_it(make_side, hq, clock):
    a, b = make_side("alpha"), make_side("beta")
    connect(a, b, hq)
    tid = make_thread(a, "Launch")
    share(a, tid, b)
    drive(a, b)
    lost = lose_messages_to(hq, a, b)
    say(a, tid, "the last word")
    drive(a, b)
    lost["on"] = False
    there = local_thread(b, tid, a)
    assert texts(b, there) == []
    with b.app.state.store.read() as c:
        assert c.execute("SELECT catch_up_requested_at FROM federation_threads WHERE thread_id=?",
                         (there,)).fetchone()[0] is None   # an active thread does not ask
    with b.app.state.store.transaction() as c:
        c.execute("UPDATE federation_threads SET updated_at='2026-01-01T00:00:00.000000Z' WHERE thread_id=?", (there,))
    drive(a, b, rounds=3)
    assert texts(b, there) == ["the last word"]


def test_a_sub_thread_is_refused_and_the_thread_itself_is_what_gets_shared(make_side, hq):
    a, b = make_side("alpha"), make_side("beta")
    connect(a, b, hq)
    tid = make_thread(a, "Launch")
    root = say(a, tid, "the root")
    with a.app.state.store.transaction() as c:
        sub = CORE.post(c, "human:ana", tid, "a reply", parent_message_id=root)["conversation_id"]
    assert sub != tid
    r = a.client.post(f"/api/v2/federation/threads/{sub}/installs", json={"install": b.id}, headers=OWNER)
    assert r.status_code == 422 and r.json()["error"]["code"] == "sub_thread"
    assert rows(a, "SELECT 1 FROM federation_members") == []
    share(a, tid, b)


def test_a_slack_link_and_sharing_with_another_company_refuse_each_other(make_side, hq):
    a, b = make_side("alpha"), make_side("beta")
    connect(a, b, hq)
    linked = make_thread(a, "Linked")
    link = {"thread_id": linked, "channel": "#launch"}
    assert a.client.post("/api/v2/slack/links", json=link, headers=OWNER).status_code == 200
    r = a.client.post(f"/api/v2/federation/threads/{linked}/installs", json={"install": b.id}, headers=OWNER)
    assert r.status_code == 409 and r.json()["error"]["code"] == "slack_linked"
    assert rows(a, "SELECT 1 FROM federation_members") == []
    tid = make_thread(a, "Launch")
    share(a, tid, b)
    drive(a, b)
    there = local_thread(b, tid, a)
    for side, thread in ((a, tid), (b, there)):              # the home's thread shared out, and the replica of it
        r = side.client.post("/api/v2/slack/links", json={"thread_id": thread, "channel": "#launch"}, headers=OWNER)
        assert r.status_code == 409 and r.json()["error"]["code"] == "shared_thread"


def test_another_companys_words_stay_out_of_webhooks_and_digests(make_side, hq):
    a, b = make_side("alpha"), make_side("beta")
    connect(a, b, hq)
    for side in (a, b):
        with side.app.state.store.transaction() as c:
            c.execute("INSERT INTO webhooks(id,name,url,host,events_json,created_by,created,updated) VALUES('ep_1','Acme',"
                      "'https://hooks.acme.example/x','hooks.acme.example','[\"*\"]','human:ana',?,?)", (H.now(), H.now()))
    tid = make_thread(a, "Secret launch title")
    share(a, tid, b)
    drive(a, b)
    there = local_thread(b, tid, a)
    words = say(a, tid, "Secret first line")
    drive(a, b)
    with b.app.state.store.transaction() as c:
        message = c.execute("SELECT id FROM messages WHERE conversation_id=?", (there,)).fetchone()[0]
        sub = CORE.post(c, "human:ana", there, "A reply here", parent_message_id=message)["conversation_id"]
        c.execute("UPDATE thread_followers SET notify='digest' WHERE actor='human:ana'")
    with a.app.state.store.transaction() as c:
        CORE.post(c, "human:ana", tid, "A reply at home", parent_message_id=words)
    drive(a, b)
    sent = [json.loads(r[0])["data"] for r in rows(b, "SELECT body FROM webhook_sends")]
    created = [d for d in sent if "title" in d]
    assert [d["id"] for d in created] == [sub] and created[0]["title"] == ""
    guests = {d["from_actor"] for d in sent if d.get("from_actor", "").endswith(a.id)}
    assert guests == {f"peer:{a.id}"}
    with b.app.state.store.transaction() as c:
        titles = {item["title"] for item in DIGEST._plan(c, "human:ana")["items"]}
    assert titles == {DIGEST.FALLBACK_TITLE}
    # The home's own title may go; a sub-thread there may hang from a message of the other company's, so its title may not.
    with a.app.state.store.read() as c:
        assert not CORE.title_from_outside(c, {"id": tid, "root_id": tid})
        home_sub = c.execute("SELECT id,root_id FROM conversations WHERE root_id=? AND id<>?", (tid, tid)).fetchone()
        assert CORE.title_from_outside(c, dict(home_sub))
    assert not any("Secret" in json.dumps(d) for d in sent)


def test_a_removed_install_gets_the_removal_and_nothing_after(make_side, hq):
    a, b = make_side("alpha"), make_side("beta")
    connect(a, b, hq)
    tid = make_thread(a, "Launch")
    share(a, tid, b)
    say(a, tid, "before")
    drive(a, b)
    response = a.client.delete(f"/api/v2/federation/threads/{tid}/installs/{b.id}", headers=OWNER)
    assert response.status_code == 200, response.text
    say(a, tid, "after the removal")
    drive(a, b, rounds=3)
    there = local_thread(b, tid, a)
    assert texts(b, there) == ["before"]
    with b.app.state.store.read() as c:
        assert c.execute("SELECT removed_at_seq FROM federation_threads WHERE thread_id=?", (there,)).fetchone()[0] is not None
    assert hq.mail.get(b.id, []) == []


def test_the_kill_switch_stops_both_directions_and_resume_repairs(make_side, hq, clock):
    a, b = make_side("alpha"), make_side("beta")
    connect(a, b, hq)
    tid = make_thread(a, "Launch")
    share(a, tid, b)
    drive(a, b)
    there = local_thread(b, tid, a)
    paused = a.client.post(f"/api/v2/federation/peerings/{row_for(a, b.id)}/pause", json={"paused": True}, headers=OWNER)
    assert paused.status_code == 200 and paused.json()["paused"] is True
    say(a, tid, "while paused")
    drive(a, b, rounds=2)
    assert "while paused" not in texts(b, there)
    say(b, there, "from beta while paused", who="human:ben")
    drive(a, b, rounds=2)
    assert "from beta while paused" not in texts(a, tid)
    resumed = a.client.post(f"/api/v2/federation/peerings/{row_for(a, b.id)}/pause", json={"paused": False}, headers=OWNER)
    assert resumed.json()["paused"] is False
    clock["t"] += 600                                    # the refused sends wait out their backoff
    drive(a, b, rounds=3)
    assert "while paused" in texts(b, there)            # what was missed is caught up on resume
    assert "from beta while paused" in texts(a, tid)    # and beta's own message reaches alpha


def test_an_envelope_whose_actor_is_in_another_install_is_refused(make_side, hq):
    a, b = make_side("alpha"), make_side("beta")
    connect(a, b, hq)
    tid = make_thread(a, "Launch")
    share(a, tid, b)
    drive(a, b)
    stranger = "human:mallory@gi_" + "c" * 24
    envelope_id = sign_and_post(hq, b, a, message_body(tid, stranger, "forged"))
    drive(a)
    assert outcomes(a, b, envelope_id) == ["namespace"]
    assert "forged" not in texts(a, tid)


def test_an_envelope_seen_twice_is_refused_the_second_time(make_side, hq):
    a, b = make_side("alpha"), make_side("beta")
    connect(a, b, hq)
    tid = make_thread(a, "Launch")
    share(a, tid, b)
    drive(a, b)
    hq.twice = True
    body = message_body(tid, "human:ben@" + b.id, "once")
    envelope_id = sign_and_post(hq, b, a, body)
    sign_and_post(hq, b, a, body, envelope_id=envelope_id)
    drive(a)
    assert texts(a, tid) == ["once"]
    assert outcomes(a, b, envelope_id) == ["applied"]    # one receipt per envelope: the second copy is refused
    assert hq.mail.get(a.id, []) == []                    # and it was taken and acknowledged like any other


# ----------------------------------------------------------------------------- the CLI and the MCP tools
PEER_COMMANDS = {
    ("list",): "peer list", ("key",): "peer key", ("rotate-key",): "peer rotate-key",
    ("request", "gi_" + "a" * 24): "peer request",
    ("approve", "fp_x"): "peer approve", ("pause", "fp_x"): "peer pause", ("resume", "fp_x"): "peer resume",
    ("revoke", "fp_x"): "peer revoke",
    ("share", "t-1", "gi_" + "a" * 24): "peer share", ("unshare", "t-1", "gi_" + "a" * 24): "peer unshare",
    ("add-reader", "t-1", "human:ben"): "peer add-reader",
    ("retry", "t-1", "gi_" + "a" * 24): "peer retry",
}


def test_every_federation_command_parses_and_has_its_mcp_tool():
    from clients import hubcli, hubtools, remotecli
    parser = hubcli.parser()
    for argv, fn in PEER_COMMANDS.items():
        args = hubcli.parse(parser, ["peer", *argv])
        assert args.fn == fn
        assert remotecli.tool_name(fn) in hubtools.BY_NAME
    writes = {name for name in hubtools.BY_NAME if name.startswith("hub_peer_")}
    assert writes == {remotecli.tool_name(fn) for fn in PEER_COMMANDS.values()}
    assert all(hubtools.BY_NAME[name]["writes"] for name in writes - {"hub_peer_list"})


def rows(side, sql, args=()):
    with side.app.state.store.read() as c:
        return [tuple(r) for r in c.execute(sql, args).fetchall()]


def receipts(side, peer):
    return [r[0] for r in rows(side, "SELECT outcome FROM federation_receipts WHERE peer_install=? ORDER BY received_at",
                               (peer.id,))]


def test_a_carried_message_is_written_as_its_remote_author(make_side, hq):
    a, b = make_side("alpha"), make_side("beta")
    connect(a, b, hq)
    tid = make_thread(a, "Launch")
    share(a, tid, b)
    say(a, tid, "hello from alpha")
    drive(a, b)
    there = local_thread(b, tid, a)
    assert rows(b, "SELECT from_actor FROM messages WHERE conversation_id=? AND body=?", (there, "hello from alpha")) == [
        (f"human:ana@{a.id}",)]


def test_a_local_person_reads_a_replica_only_once_an_admin_adds_them(make_side, hq):
    a, b = make_side("alpha"), make_side("beta")
    connect(a, b, hq)
    tid = make_thread(a, "Launch")
    share(a, tid, b)
    say(a, tid, "hello from alpha")
    drive(a, b)
    there = local_thread(b, tid, a)
    assert b.client.get(f"/api/v2/threads/{there}", headers=BEN).status_code == 404
    assert b.client.get(f"/api/v2/threads/{there}", headers=OWNER).status_code == 200    # the admin who accepted the peering
    assert b.client.post(f"/api/v2/federation/threads/{there}/readers", json={"actor": "human:ben"},
                         headers=BEN).status_code == 403
    added = b.client.post(f"/api/v2/federation/threads/{there}/readers", json={"actor": "human:ben"}, headers=OWNER)
    assert added.status_code == 200, added.text
    assert b.client.get(f"/api/v2/threads/{there}", headers=BEN).status_code == 200


def test_the_homes_roster_reaches_the_replica_as_guest_rows_and_leaves_with_them(make_side, hq):
    a, b = make_side("alpha"), make_side("beta")
    connect(a, b, hq)
    tid = make_thread(a, "Launch")
    share(a, tid, b)
    drive(a, b)
    there = local_thread(b, tid, a)

    def guests():
        return [r[0] for r in rows(b, "SELECT actor FROM thread_members WHERE thread_id=?", (there,)) if r[0].endswith("@" + a.id)]

    with a.app.state.store.transaction() as c:
        CORE.add_member(c, "keeper", tid, "human:ben")
    drive(a, b, rounds=3)
    assert sorted(guests()) == ["human:ana@" + a.id, "human:ben@" + a.id]
    with a.app.state.store.transaction() as c:
        CORE.remove_member(c, "keeper", tid, "human:ben")
    drive(a, b, rounds=3)
    assert guests() == ["human:ana@" + a.id]


def test_a_message_the_org_rules_block_is_written_nowhere_and_its_author_is_told(make_side, hq, monkeypatch):
    real_apply = OR.apply

    def apply(c, verdict, payload=None, replay=None, tell=False, obj_now=None):
        if "BLOCKED" in str(payload or ""):
            return {"held": {"id": "hold"}}
        return real_apply(c, verdict, payload, replay, tell, obj_now)

    monkeypatch.setattr(OR, "apply", apply)
    a, b = make_side("alpha"), make_side("beta")
    connect(a, b, hq)
    tid = make_thread(a, "Launch")
    share(a, tid, b)
    drive(a, b)
    there = local_thread(b, tid, a)
    reader(b, there, "human:ben")
    say(b, there, "BLOCKED: the draft plan", who="human:ben")
    drive(a, b, rounds=4)
    assert "BLOCKED: the draft plan" not in texts(a, tid)
    assert "rules" in receipts(a, b)
    assert ("human:ben",) in rows(b, "SELECT to_actor FROM messages WHERE conversation_id=? AND kind='notice'", (there,))


def test_a_replica_refuses_a_carried_message_its_own_rules_block(make_side, hq, monkeypatch):
    real_apply = OR.apply

    def apply(c, verdict, payload=None, replay=None, tell=False, obj_now=None):
        if "BLOCKED" in str(payload or ""):
            return {"held": {"id": "hold"}}
        return real_apply(c, verdict, payload, replay, tell, obj_now)

    monkeypatch.setattr(OR, "apply", apply)
    a, b = make_side("alpha"), make_side("beta")
    connect(a, b, hq)
    tid = make_thread(a, "Launch")
    share(a, tid, b)
    drive(a, b)
    there = local_thread(b, tid, a)
    reader(a, tid, "human:ana")
    say(a, tid, "BLOCKED: the home's post")
    drive(a, b, rounds=4)
    assert "BLOCKED: the home's post" not in texts(b, there)
    assert "rules" in receipts(b, a)
    assert ("human:ana",) in rows(a, "SELECT to_actor FROM messages WHERE conversation_id=? AND kind='notice'", (tid,))


def test_a_remote_guest_bot_never_wakes_here(make_side, hq):
    a, b = make_side("alpha"), make_side("beta")
    connect(a, b, hq)
    tid = make_thread(a, "Launch")
    share(a, tid, b)
    drive(a, b)
    there = local_thread(b, tid, a)
    with a.app.state.store.transaction() as c:
        c.execute("INSERT INTO thread_members(id,thread_id,actor,role,added_by,created_at,updated_at) VALUES(?,?,?,?,?,?,?)",
                  (H.new_id(), tid, "bot:scout", "member", H.KEEPER, H.now(), H.now()))
    drive(a, b, rounds=3)
    guest = f"bot:scout@{a.id}"
    assert (guest,) in rows(b, "SELECT actor FROM thread_members WHERE thread_id=?", (there,))
    reader(b, there, "human:ben")
    with b.app.state.store.transaction() as c:
        with pytest.raises(Problem) as refused:
            CORE.post(c, "human:ben", there, "hello scout", to_actor=guest)
    assert refused.value.code == "not_found"        # no local bot has that name, so nothing here can address it
    say(a, tid, "hello team")
    drive(a, b, rounds=3)
    assert "hello team" in texts(b, there)
    assert rows(b, "SELECT COUNT(*) FROM jobs WHERE instr(bot, '@') > 0") == [(0,)]


def test_each_side_sees_the_company_its_peer_gave_and_guests_carry_theirs(make_side, hq):
    a, b = make_side("alpha"), make_side("beta")
    connect(a, b, hq)
    assert status(a)["peerings"][0]["company"] == "Beta Co"
    assert status(b)["peerings"][0]["company"] == "Alpha Co"
    tid = make_thread(a, "Launch")
    with a.app.state.store.transaction() as c:
        CORE.add_member(c, "keeper", tid, "human:ben")
    share(a, tid, b)
    drive(a, b)
    there = local_thread(b, tid, a)
    home = next(t for t in status(a)["threads"] if t["thread_id"] == tid)
    assert {"install": b.id, "company": "Beta Co"} in [{"install": m["install"], "company": m["company"]} for m in home["members"]]
    replica = next(t for t in status(b)["threads"] if t["thread_id"] == there)
    assert {"actor": f"human:ben@{a.id}", "company": "Alpha Co"} in replica["guests"]


def test_a_refusal_for_an_author_who_can_no_longer_read_is_recorded_refused_and_no_note_is_posted(make_side, hq, monkeypatch):
    real_apply = OR.apply

    def apply(c, verdict, payload=None, replay=None, tell=False, obj_now=None):
        if "BLOCKED" in str(payload or ""):
            return {"held": {"id": "hold"}}
        return real_apply(c, verdict, payload, replay, tell, obj_now)

    monkeypatch.setattr(OR, "apply", apply)
    a, b = make_side("alpha"), make_side("beta")
    connect(a, b, hq)
    tid = make_thread(a, "Launch")
    reader(a, tid, "human:ben")
    share(a, tid, b)
    drive(a, b)
    say(a, tid, "BLOCKED: ben's draft", who="human:ben")
    with a.app.state.store.transaction() as c:
        CORE.remove_member(c, "keeper", tid, "human:ben")          # ben can no longer read the thread when the refusal lands
    drive(a, b, rounds=4)
    assert "refused" in receipts(a, b)
    assert rows(a, "SELECT COUNT(*) FROM messages WHERE conversation_id=? AND kind='notice'", (tid,)) == [(0,)]


def test_a_peer_cannot_edit_delete_or_reuse_an_origin_it_did_not_write(make_side, hq):
    a, b = make_side("alpha"), make_side("beta")
    connect(a, b, hq)
    tid = make_thread(a, "Launch")
    share(a, tid, b)
    drive(a, b)
    there = local_thread(b, tid, a)
    reader(b, there, "human:ben")
    say(b, there, "ben's note", who="human:ben")
    drive(a, b)
    assert texts(a, tid) == ["ben's note"]
    origin = rows(a, "SELECT origin_id FROM federation_messages WHERE author=?", (f"human:ben@{b.id}",))[0][0]
    eve = "human:eve@" + b.id                                  # a name beta may use, but not one that wrote the note
    sign_and_post(hq, b, a, forged(tid, eve, origin, 0, text="reused origin"))
    sign_and_post(hq, b, a, forged(tid, eve, origin, 1, text="forged edit"))
    sign_and_post(hq, b, a, forged(tid, eve, origin, 2, deleted=True))
    drive(a, b)
    assert texts(a, tid) == ["ben's note"]
    assert "origin" in receipts(a, b)
    assert receipts(a, b).count("unknown_message") == 2


def test_readers_are_added_only_to_a_replica_of_a_members_thread(make_side, hq):
    a, b = make_side("alpha"), make_side("beta")
    connect(a, b, hq)
    tid = make_thread(a, "Launch")
    share(a, tid, b)
    drive(a, b)
    there = local_thread(b, tid, a)
    local = make_thread(b, "Only here")
    with b.app.state.store.transaction() as c:
        personal = CORE.create(c, "keeper", title="Private", audience="personal", members=["human:ben"])["id"]
    for side, thread in ((a, tid), (b, local)):
        refused = side.client.post(f"/api/v2/federation/threads/{thread}/readers", json={"actor": "human:ben"}, headers=OWNER)
        assert refused.status_code == 409, refused.text
    hidden = b.client.post(f"/api/v2/federation/threads/{personal}/readers", json={"actor": "human:ben"}, headers=OWNER)
    assert hidden.status_code == 404, hidden.text                # the owner is not in it, so it does not exist for them
    removed = a.client.delete(f"/api/v2/federation/threads/{tid}/installs/{b.id}", headers=OWNER)
    assert removed.status_code == 200, removed.text
    drive(a, b)
    after = b.client.post(f"/api/v2/federation/threads/{there}/readers", json={"actor": "human:ben"}, headers=OWNER)
    assert after.status_code == 409, after.text


def test_an_install_that_joins_later_gets_no_earlier_message_and_no_stall(make_side, hq):
    a, b, c_side = make_side("alpha"), make_side("beta"), make_side("gamma")
    connect(a, b, hq)
    connect(a, c_side, hq)
    tid = make_thread(a, "Launch")
    share(a, tid, b)
    say(a, tid, "before gamma")                              # waiting for beta when gamma is added
    share(a, tid, c_side)
    drive(a, b, c_side)
    say(a, tid, "after gamma")
    drive(a, b, c_side)
    assert texts(c_side, local_thread(c_side, tid, a)) == ["after gamma"]
    assert texts(b, local_thread(b, tid, a)) == ["before gamma", "after gamma"]


def test_a_restricted_message_is_never_sent_to_a_peer(make_side, hq):
    a, b = make_side("alpha"), make_side("beta")
    connect(a, b, hq)
    tid = make_thread(a, "Launch")
    share(a, tid, b)
    drive(a, b)
    secret = say(a, tid, "task provenance")
    with a.app.state.store.transaction() as c:
        c.execute("UPDATE messages SET restricted=1 WHERE id=?", (secret,))
    say(a, tid, "plain")
    drive(a, b)
    assert texts(b, local_thread(b, tid, a)) == ["plain"]


def test_an_edit_made_before_the_first_send_sends_the_current_text(make_side, hq):
    a, b = make_side("alpha"), make_side("beta")
    connect(a, b, hq)
    tid = make_thread(a, "Launch")
    share(a, tid, b)
    drive(a, b)
    message = say(a, tid, "first draft")
    edit(a, message, "first, edited")
    drive(a, b)
    assert texts(b, local_thread(b, tid, a)) == ["first, edited"]


def test_a_replica_message_changed_after_staging_sends_only_what_is_current(make_side, hq):
    a, b = make_side("alpha"), make_side("beta")
    connect(a, b, hq)
    tid = make_thread(a, "Launch")
    share(a, tid, b)
    drive(a, b)
    there = local_thread(b, tid, a)
    gone = say(b, there, "gone", who="human:ana")
    kept = say(b, there, "draft", who="human:ana")
    with b.app.state.store.transaction() as c:
        S.stage(c, b.app.state.vault, R.now())              # waiting for the home, not yet sent
    with b.app.state.store.transaction() as c:
        CORE.delete_message(c, "human:ana", gone)
    edit(b, kept, "draft, edited", who="human:ana")
    drive(a, b)
    assert texts(a, tid) == ["draft, edited"]


def test_a_guest_from_a_third_install_is_written_under_its_own_name_on_every_replica(make_side, hq):
    a, b, c_side = make_side("alpha"), make_side("beta"), make_side("gamma")
    connect(a, b, hq)
    connect(a, c_side, hq)
    tid = make_thread(a, "Launch")
    share(a, tid, b)
    share(a, tid, c_side)
    drive(a, b, c_side)
    there_c = local_thread(c_side, tid, a)
    reader(c_side, there_c, "human:ben")
    say(c_side, there_c, "from gamma", who="human:ben")
    drive(a, b, c_side)
    guest = f"human:ben@{c_side.id}"
    assert rows(b, "SELECT from_actor FROM messages WHERE conversation_id=? AND body=?",
                (local_thread(b, tid, a), "from gamma")) == [(guest,)]
    assert rows(a, "SELECT from_actor FROM messages WHERE conversation_id=? AND body=?", (tid, "from gamma")) == [(guest,)]


def test_a_paused_peer_defers_what_waits_and_resume_applies_it(make_side, hq, clock):
    a, b = make_side("alpha"), make_side("beta")
    connect(a, b, hq)
    tid = make_thread(a, "Launch")
    share(a, tid, b)
    drive(a, b)
    there = local_thread(b, tid, a)
    say(a, tid, "held while paused")
    tick(a)                                                  # sent to HQ before the pause
    paused = a.client.post(f"/api/v2/federation/peerings/{row_for(a, b.id)}/pause", json={"paused": True}, headers=OWNER)
    assert paused.status_code == 200
    tick(b)
    assert held_reasons(b) == ["paused"]                     # HQ hands it over; this install holds it, and acks it
    assert "held while paused" not in texts(b, there)
    resumed = a.client.post(f"/api/v2/federation/peerings/{row_for(a, b.id)}/pause", json={"paused": False}, headers=OWNER)
    assert resumed.status_code == 200
    clock["t"] += 600
    drive(a, b, rounds=3)
    assert "held while paused" in texts(b, there)
    assert held_reasons(b) == []


def test_an_org_check_that_did_not_run_defers_the_text_until_it_can(make_side, hq, monkeypatch, clock):
    a, b = make_side("alpha"), make_side("beta")
    connect(a, b, hq)
    tid = make_thread(a, "Launch")
    share(a, tid, b)
    drive(a, b)
    there = local_thread(b, tid, a)
    real = T.checked_inbound
    monkeypatch.setattr(T, "checked_inbound", lambda store, judge, actor, text, install, origin_id, thread_id:
                        {"outcome": "unchecked", "error": "no_engine"} if "UNCHECKED" in text
                        else real(store, judge, actor, text, install, origin_id, thread_id))
    say(a, tid, "UNCHECKED: waiting for the model")
    tick(a)
    tick(b)
    assert held_reasons(b) == ["rules_unchecked"]
    assert "UNCHECKED: waiting for the model" not in texts(b, there)
    monkeypatch.setattr(T, "checked_inbound", real)
    clock["t"] += 600
    drive(a, b)
    assert texts(b, there) == ["UNCHECKED: waiting for the model"]


def test_a_paused_peer_holds_only_its_own_root_while_other_peers_and_roots_flow(make_side, hq, clock, monkeypatch):
    a, b, c = make_side("alpha"), make_side("beta"), make_side("gamma")
    connect(a, b, hq)
    connect(a, c, hq)
    first, second = make_thread(a, "Launch"), make_thread(a, "Press")
    share(a, first, b)
    share(a, second, c)
    drive(a, b, c)
    there_b, there_c = local_thread(b, first, a), local_thread(c, second, a)
    attempts = []                                        # (recipient, envelope id) of every send HQ receives
    real = hq.envelope

    def spy(me, request):
        attempts.append((request.headers.get("x-tico-to"), request.headers.get("x-tico-envelope")))
        return real(me, request)

    monkeypatch.setattr(hq, "envelope", spy)
    paused = a.client.post(f"/api/v2/federation/peerings/{row_for(a, b.id)}/pause", json={"paused": True}, headers=OWNER)
    assert paused.status_code == 200
    say(a, first, "A1 while paused")
    say(a, second, "C1 other root")
    drive(a, c, rounds=2)
    assert texts(c, there_c) == ["C1 other root"]        # another peer's root is not held back
    say(a, first, "A2 same root")
    for _ in range(3):
        clock["t"] += 600
        tick(a)
    assert [env for recipient, env in attempts if recipient == b.id] == []   # nothing goes to a paused peer: A1 and A2 wait
    assert texts(b, there_b) == []
    resumed = a.client.post(f"/api/v2/federation/peerings/{row_for(a, b.id)}/pause", json={"paused": False}, headers=OWNER)
    assert resumed.status_code == 200
    clock["t"] += 600
    attempts.clear()
    drive(a, b, rounds=3)
    assert texts(b, there_b) == ["A1 while paused", "A2 same root"]
    assert len(set(env for recipient, env in attempts if recipient == b.id)) == 2   # both sent once the peer resumes


def test_a_transient_failure_holds_the_later_post_to_the_same_peer_and_root(make_side, hq, clock, monkeypatch):
    a, b = make_side("alpha"), make_side("beta")
    connect(a, b, hq)
    tid = make_thread(a, "Launch")
    share(a, tid, b)
    drive(a, b)
    there = local_thread(b, tid, a)
    attempts = []
    real = hq.envelope
    unavailable = {"left": 1}

    def spy(me, request):
        if request.headers.get("x-tico-to") == b.id:
            attempts.append(request.headers.get("x-tico-envelope"))
            if unavailable["left"]:
                unavailable["left"] -= 1
                return httpx.Response(503, json={"error": "unavailable"})
        return real(me, request)

    monkeypatch.setattr(hq, "envelope", spy)
    say(a, tid, "first")
    tick(a)                                              # HQ does not take it: it waits for its backoff
    say(a, tid, "second")
    tick(a)
    assert len(attempts) == 1                            # second is not sent past first while first waits
    clock["t"] += 600
    drive(a, b, rounds=2)
    assert texts(b, there) == ["first", "second"]
    assert len(attempts) == 3                            # first (refused once, then taken), then second


def test_a_held_envelope_holds_only_its_own_root_from_that_peer(make_side, hq, clock, monkeypatch):
    a, b = make_side("alpha"), make_side("beta")
    connect(a, b, hq)
    one, two = make_thread(a, "Launch"), make_thread(a, "Press")
    share(a, one, b)
    share(a, two, b)
    drive(a, b)
    there_one, there_two = local_thread(b, one, a), local_thread(b, two, a)
    real = T.checked_inbound
    monkeypatch.setattr(T, "checked_inbound", lambda store, judge, actor, text, install, origin_id, thread_id:
                        {"outcome": "unchecked", "error": "no_engine"} if "UNCHECKED" in text
                        else real(store, judge, actor, text, install, origin_id, thread_id))
    say(a, one, "UNCHECKED: waiting for the model")
    tick(a)
    tick(b)
    assert held_reasons(b) == ["rules_unchecked"]
    say(a, two, "other root")
    drive(a, b, rounds=2)
    assert texts(b, there_two) == ["other root"]         # another root from the same peer goes on
    say(a, one, "after the held one")
    drive(a, b, rounds=2)
    assert texts(b, there_one) == []
    assert held_reasons(b) == ["rules_unchecked", "behind_earlier"]   # the same root waits behind the held one
    monkeypatch.setattr(T, "checked_inbound", real)
    clock["t"] += 600
    drive(a, b, rounds=3)
    assert texts(b, there_one) == ["UNCHECKED: waiting for the model", "after the held one"]
    assert held_reasons(b) == []


def test_a_sub_thread_reaches_the_other_install_both_ways(make_side, hq):
    a, b = make_side("alpha"), make_side("beta")
    connect(a, b, hq)
    tid = make_thread(a, "Launch")
    reader(a, tid, "human:ana")
    share(a, tid, b)
    root = say(a, tid, "root post")
    drive(a, b)
    there = local_thread(b, tid, a)
    parent_b = rows(b, "SELECT id FROM messages WHERE conversation_id=? AND body=?", (there, "root post"))[0][0]
    with a.app.state.store.transaction() as c:
        CORE.post(c, "human:ana", tid, "reply from alpha", parent_message_id=root)
    drive(a, b)
    assert rows(b, "SELECT c.parent_message_id FROM messages m JOIN conversations c ON c.id=m.conversation_id "
                   "WHERE m.body=?", ("reply from alpha",)) == [(parent_b,)]
    with b.app.state.store.transaction() as c:
        CORE.post(c, "human:ana", there, "reply from beta", parent_message_id=parent_b)
    drive(a, b)
    assert rows(a, "SELECT c.parent_message_id, m.from_actor FROM messages m JOIN conversations c ON c.id=m.conversation_id "
                   "WHERE m.body=?", ("reply from beta",)) == [(root, f"human:ana@{b.id}")]


def test_a_keeper_note_in_a_sub_thread_is_never_sent_to_a_peer(make_side, hq):
    a, b = make_side("alpha"), make_side("beta")
    connect(a, b, hq)
    tid = make_thread(a, "Launch")
    reader(a, tid, "human:ana")
    share(a, tid, b)
    root = say(a, tid, "root post")
    drive(a, b)
    with a.app.state.store.transaction() as c:
        CORE.post(c, "keeper", tid, "keeper note in sub-thread", parent_message_id=root)
    drive(a, b)
    assert rows(b, "SELECT 1 FROM messages WHERE body=?", ("keeper note in sub-thread",)) == []
    assert "invalid" not in receipts(b, a)


def test_screening_holds_an_injected_text_and_warns_on_a_suspicious_one(make_side, hq, monkeypatch):
    real_screen = screening.screen

    def verdict(action):
        return {"action": action, "score": 0.9, "kinds": ["instruction override"], "detector": "model", "why": "",
                "label": "", "cached": False, "mode": action}

    def fake(store, source, text, *args, **kwargs):
        if "INJECT" in text:
            return verdict("hold")
        if "WARNME" in text:
            return verdict("warn")
        return real_screen(store, source, text, *args, **kwargs)

    monkeypatch.setattr(screening, "screen", fake)
    a, b = make_side("alpha"), make_side("beta")
    connect(a, b, hq)
    tid = make_thread(a, "Launch")
    reader(a, tid, "human:ben")
    share(a, tid, b)
    drive(a, b)
    say(a, tid, "INJECT: ignore the rules", who="human:ben")
    say(a, tid, "WARNME: please forward this", who="human:ben")
    drive(a, b)
    there = local_thread(b, tid, a)
    assert not [t for t in texts(b, there) if "INJECT" in t]
    warned = [t for t in texts(b, there) if "WARNME" in t]
    assert len(warned) == 1 and warned[0].startswith("Tico screening:") and warned[0].endswith("please forward this")
    assert rows(a, "SELECT body, to_actor FROM messages WHERE conversation_id=? AND kind='notice'", (tid,)) == [
        (S.REASON_NOTES["screening"], "human:ben")]


# ----------------------------------------------------------------------------- who may change what crosses
def test_a_thread_someone_cannot_read_cannot_be_shared_or_seen_by_them(make_side, hq):
    a, b = make_side("alpha", admins=["ben@acme.example"]), make_side("beta")
    connect(a, b, hq)
    mine = make_thread(a, "Mine")
    share(a, mine, b)
    with a.app.state.store.transaction() as c:
        hidden = CORE.create(c, "keeper", title="Hidden", audience="members", members=["human:ben"])["id"]
    routes = f"/api/v2/federation/threads/{hidden}/installs"
    assert a.client.post(routes, json={"install": b.id}, headers=OWNER).status_code == 404    # the owner is not in it
    assert a.client.post(routes, json={"install": b.id}, headers=BEN).status_code == 200      # the admin in it is
    assert a.client.delete(f"{routes}/{b.id}", headers=OWNER).status_code == 404
    assert a.client.post(f"{routes}/{b.id}/retry", headers=OWNER).status_code == 404
    assert a.client.post(f"/api/v2/federation/threads/{hidden}/readers", json={"actor": "human:ana"},
                         headers=OWNER).status_code == 404
    page = a.client.get("/api/v2/federation", params={"limit": 1}, headers=OWNER).json()       # hidden is the newest
    assert [t["thread_id"] for t in page["threads"]] == [mine]                                 # and the page stays full
    assert hidden in [t["thread_id"] for t in a.client.get("/api/v2/federation", headers=BEN).json()["threads"]]


def code_of(response):
    return response.json()["error"]["code"]


def keyed(who=OWNER):
    return {**who, "Idempotency-Key": uuid.uuid4().hex}


def test_a_shared_thread_stays_a_members_thread_on_both_sides_and_for_good_on_a_copy(make_side, hq):
    a, b = make_side("alpha"), make_side("beta")
    connect(a, b, hq)
    tid = make_thread(a, "Launch")
    share(a, tid, b)
    drive(a, b)
    there = local_thread(b, tid, a)
    to_company = {"audience": "company"}
    home = a.client.patch(f"/api/v2/threads/{tid}", json=to_company, headers=keyed())
    assert (home.status_code, code_of(home)) == (409, "shared_audience")
    copy = b.client.patch(f"/api/v2/threads/{there}", json=to_company, headers=keyed())
    assert (copy.status_code, code_of(copy)) == (409, "shared_audience")
    assert a.client.delete(f"/api/v2/federation/threads/{tid}/installs/{b.id}", headers=OWNER).status_code == 200
    drive(a, b)
    assert a.client.patch(f"/api/v2/threads/{tid}", json=to_company, headers=keyed()).status_code == 200   # back to its own
    assert b.client.patch(f"/api/v2/threads/{there}", json=to_company, headers=keyed()).status_code == 409  # another's words


def test_companies_come_and_go_through_sharing_not_a_threads_member_list(make_side, hq):
    a, b = make_side("alpha"), make_side("beta")
    connect(a, b, hq)
    tid = make_thread(a, "Launch")
    share(a, tid, b)
    with a.app.state.store.transaction() as c:
        CORE.add_member(c, "keeper", tid, "human:ben")
    drive(a, b, rounds=3)
    there = local_thread(b, tid, a)

    def add(side, thread, actor, who=OWNER):
        return side.client.post(f"/api/v2/threads/{thread}/members", json={"actor": actor}, headers=keyed(who))

    def remove(side, thread, actor, who=OWNER):
        return side.client.delete(f"/api/v2/threads/{thread}/members/{actor}", headers=keyed(who))

    assert code_of(remove(a, tid, f"peer:{b.id}")) == "shared_membership"
    assert code_of(add(a, make_thread(a, "Other"), f"peer:{b.id}")) == "shared_membership"
    assert code_of(add(a, tid, f"human:eve@{b.id}")) == "shared_membership"
    assert code_of(remove(b, there, f"human:ben@{a.id}")) == "shared_membership"     # the home decides who of theirs is in
    assert (f"human:ben@{a.id}",) in rows(b, "SELECT actor FROM thread_members WHERE thread_id=?", (there,))
    assert add(b, there, "human:ben").status_code == 200                             # the copy's own readers are its admins'
    assert add(b, there, "human:ana", who=BEN).status_code == 403
    assert remove(b, there, "human:ben").status_code == 200


# ----------------------------------------------------------------------------- an edit is never made into a message
def test_an_edit_of_a_message_the_home_never_had_is_refused_and_no_text_lands(make_side, hq):
    a, b = make_side("alpha"), make_side("beta")
    connect(a, b, hq)
    tid = make_thread(a, "Launch")
    share(a, tid, b)
    drive(a, b)
    ben = f"human:ben@{b.id}"
    edit_envelope = sign_and_post(hq, b, a, message_body(tid, ben, "edited text, no original", revision=3))
    drive(a)
    assert outcomes(a, b, edit_envelope) == ["unknown_message"]
    assert texts(a, tid) == []
    first = E.payload_bytes({"type": "message", "thread": tid, "origin_id": "msg-" + uuid.uuid4().hex, "revision": 3,
                             "actor": ben, "message_kind": "say", "body": "edited before it first went", "deleted": False,
                             "created": "", "first": True})
    sign_and_post(hq, b, a, first)
    drive(a)
    assert texts(a, tid) == ["edited before it first went"]    # the first send of a message, at the revision it has by then


def test_a_copy_never_makes_a_message_of_an_edit_and_waits_for_the_original(make_side, hq):
    a, b = make_side("alpha"), make_side("beta")
    connect(a, b, hq)
    tid = make_thread(a, "Launch")
    share(a, tid, b)
    drive(a, b)
    there = local_thread(b, tid, a)
    lost = {"on": True}

    def lose_the_first_send(sender, to, body):
        if sender == a.id and to == b.id and lost["on"] and json.loads(body).get("item_type") == "message":
            lost["on"] = False
            return True
        return False

    hq.lose = lose_the_first_send
    message = say(a, tid, "first")
    tick(a)
    edit(a, message, "first, edited")
    tick(a)
    tick(b)                                # only the edit came: the gap asks for a catch-up, and nothing is written
    assert texts(b, there) == []
    edit_only = {**S.checked_message(json.loads(forged(tid, f"human:ana@{a.id}", "msg-" + uuid.uuid4().hex, 2, text="no original"))),
                 "first": False}
    with b.app.state.store.transaction() as c:
        thread = S.thread_local(c, there)
        assert S.apply_message(c, b.id, thread, a.id, thread["applied_through"] + 1, edit_only, sender=a.id) == "unknown_message"
    assert texts(b, there) == []
    drive(a, b)
    assert texts(b, there) == ["first, edited"]


# ----------------------------------------------------------------------------- a message held by this install's rules
def ruled(side):
    side.app.state.judge = Judge(side.app.state.store)
    made = side.client.post("/api/v2/rules", json={"kind": "internal", "text": COLOR, "who": {"everyone": True}}, headers=keyed())
    assert made.status_code == 200, made.text


def holds(side, state="held"):
    return [dict(zip(("id", "author", "operation"), r)) for r in rows(
        side, "SELECT id, author, operation FROM org_rule_holds WHERE state=? AND operation='federated_message' "
              "ORDER BY created", (state,))]


def send_anyway(side, hold, expected=200):
    view = side.client.get(f"/api/v2/rule-holds/{hold['id']}", headers=OWNER)
    assert view.status_code == 200 and view.json()["can_send"], view.text
    sent = side.client.post(f"/api/v2/rule-holds/{hold['id']}/send", json={"nonce": view.json()["nonce"]}, headers=keyed())
    assert sent.status_code == expected, sent.text
    return view.json()["nonce"]


def needs_kinds(side, token=OWNER):
    return [i["kind"] for i in side.client.get("/api/v2/needs-you", headers=token).json()["items"]]


def test_send_anyway_releases_a_replicas_message_held_at_home_as_its_author_to_every_copy(make_side, hq):
    a, b, third = make_side("alpha"), make_side("beta"), make_side("gamma")
    connect(a, b, hq)
    connect(a, third, hq)
    tid = make_thread(a, "Launch")
    share(a, tid, b)
    share(a, tid, third)
    drive(a, b, third)
    there_b, there_third = local_thread(b, tid, a), local_thread(third, tid, a)
    ruled(a)
    say(b, there_b, "Our favorite color is gold.", who="human:ana")
    drive(a, b, third)
    author = f"human:ana@{b.id}"
    assert texts(a, tid) == [] and texts(third, there_third) == []
    assert "rules" in receipts(a, b)                          # the sender is told, as for any refusal
    [hold] = holds(a)
    assert hold["author"] == author and "thread_held" in needs_kinds(a)
    nonce = send_anyway(a, hold)
    drive(a, b, third)
    assert rows(a, "SELECT from_actor, body FROM messages WHERE conversation_id=? AND kind<>'notice'", (tid,)) == [
        (author, "Our favorite color is gold.")]
    # The other company checks the author's own signature, and the author's copy is not written twice.
    assert rows(third, "SELECT from_actor FROM messages WHERE conversation_id=?", (there_third,)) == [(author,)]
    assert [t for t in texts(b, there_b) if t == "Our favorite color is gold."] == ["Our favorite color is gold."]
    assert holds(a) == [] and "thread_held" not in needs_kinds(a)
    again = a.client.post(f"/api/v2/rule-holds/{hold['id']}/send", json={"nonce": nonce}, headers=keyed())
    assert again.status_code in (404, 409)                    # spent, and the message is there once
    assert texts(a, tid).count("Our favorite color is gold.") == 1


def test_send_anyway_releases_a_message_held_on_a_copy_first_or_edited(make_side, hq):
    a, b = make_side("alpha"), make_side("beta")
    connect(a, b, hq)
    tid = make_thread(a, "Launch")
    share(a, tid, b)
    drive(a, b)
    there = local_thread(b, tid, a)
    ruled(b)
    say(a, tid, "Our favorite color is gold.")                # the first of a message
    drive(a, b)
    assert texts(b, there) == []
    [held_first] = holds(b)
    send_anyway(b, held_first)
    assert rows(b, "SELECT from_actor, body FROM messages WHERE conversation_id=?", (there,)) == [
        (f"human:ana@{a.id}", "Our favorite color is gold.")]
    plain = say(a, tid, "Plain so far.")                      # a revision of a message already here
    drive(a, b)
    edit(a, plain, "Their favorite color is blue.")
    drive(a, b)
    assert "Their favorite color is blue." not in texts(b, there) and "Plain so far." in texts(b, there)
    [held_edit] = holds(b)
    send_anyway(b, held_edit)
    assert "Their favorite color is blue." in texts(b, there) and "Plain so far." not in texts(b, there)
    say(a, tid, "Later and fine.")
    drive(a, b)
    assert texts(b, there)[-1] == "Later and fine."           # the stream goes on from where it was


def test_a_later_revision_of_a_held_message_closes_the_hold_and_is_checked_on_its_own(make_side, hq):
    a, b = make_side("alpha"), make_side("beta")
    connect(a, b, hq)
    tid = make_thread(a, "Launch")
    share(a, tid, b)
    drive(a, b)
    there = local_thread(b, tid, a)
    ruled(b)
    message = say(a, tid, "Our favorite color is gold.")
    drive(a, b)
    [first] = holds(b)
    nonce = b.client.get(f"/api/v2/rule-holds/{first['id']}", headers=OWNER).json()["nonce"]
    edit(a, message, "Our favorite color is red.")
    drive(a, b)
    [second] = holds(b)                                       # still blocked: a new hold, on the new words
    assert second["id"] != first["id"] and first["id"] in [h["id"] for h in holds(b, "kept")]
    late = b.client.post(f"/api/v2/rule-holds/{first['id']}/send", json={"nonce": nonce}, headers=keyed())
    assert late.status_code in (404, 409) and texts(b, there) == []
    edit(a, message, "We cannot say.")
    drive(a, b)
    assert holds(b) == [] and texts(b, there) == ["We cannot say."]   # it passes, so it is written, as the first it has


def test_a_catch_up_never_files_a_second_hold_for_the_same_words(make_side, hq, clock):
    a, b = make_side("alpha"), make_side("beta")
    connect(a, b, hq)
    tid = make_thread(a, "Launch")
    share(a, tid, b)
    drive(a, b)
    there = local_thread(b, tid, a)
    ruled(b)
    message = say(a, tid, "Plain so far.")
    drive(a, b)
    edit(a, message, "Our favorite color is gold.")
    drive(a, b)
    [hold] = holds(b)
    assert b.client.post(f"/api/v2/rule-holds/{hold['id']}/keep", json={}, headers=keyed()).status_code == 200
    clock["t"] += 600
    with b.app.state.store.transaction() as c:
        S.request_catch_up(c, R.now(), S.thread_local(c, there), 0)
    drive(a, b)
    assert holds(b) == [] and texts(b, there) == ["Plain so far."]      # the reviewer's answer stands
    assert held_reasons(b) == []                                        # and the lane is not stuck behind it


def test_a_hold_stays_held_while_the_peering_is_paused(make_side, hq):
    a, b = make_side("alpha"), make_side("beta")
    connect(a, b, hq)
    tid = make_thread(a, "Launch")
    share(a, tid, b)
    drive(a, b)
    there = local_thread(b, tid, a)
    ruled(a)
    say(b, there, "Our favorite color is gold.", who="human:ana")
    drive(a, b)
    [hold] = holds(a)
    assert a.client.post(f"/api/v2/federation/peerings/{row_for(a, b.id)}/pause", json={"paused": True},
                         headers=OWNER).status_code == 200
    send_anyway(a, hold, expected=409)
    assert holds(a) == [hold] and texts(a, tid) == []
    assert a.client.post(f"/api/v2/federation/peerings/{row_for(a, b.id)}/pause", json={"paused": False},
                         headers=OWNER).status_code == 200
    send_anyway(a, hold)                                      # its nonce was not spent
    assert texts(a, tid) == ["Our favorite color is gold."]


def test_the_injection_screen_goes_first_so_a_hold_never_carries_a_text_it_stopped(make_side, hq, monkeypatch):
    real_screen = screening.screen
    monkeypatch.setattr(screening, "screen", lambda store, source, text, *args, **kwargs: (
        {"action": "hold", "score": 0.9, "kinds": ["instruction override"], "detector": "model", "why": "", "label": "",
         "cached": False, "mode": "hold"} if "INJECT" in text else real_screen(store, source, text, *args, **kwargs)))
    a, b = make_side("alpha"), make_side("beta")
    connect(a, b, hq)
    tid = make_thread(a, "Launch")
    share(a, tid, b)
    drive(a, b)
    there = local_thread(b, tid, a)
    ruled(b)
    say(a, tid, "INJECT: our favorite color is gold.")
    drive(a, b)
    assert texts(b, there) == [] and holds(b) == [] and "screening" in receipts(b, a)


# ----------------------------------------------------------------------------- BotOps acts with the person's rights
def test_botops_runs_every_federation_write_and_tool_as_the_person_who_asked():
    from backend.botops_act import default_delegable
    from clients import hubtools
    for method, path in [("POST", "federation/keys"), ("POST", "federation/keys/rotate"), ("POST", "federation/peerings"),
                         ("POST", "federation/peerings/fp_x/approve"), ("POST", "federation/peerings/fp_x/pause"),
                         ("POST", "federation/peerings/fp_x/revoke"), ("POST", "federation/threads/t1/installs"),
                         ("DELETE", "federation/threads/t1/installs/gi_x"), ("POST", "federation/threads/t1/installs/gi_x/retry"),
                         ("POST", "federation/threads/t1/readers")]:
        assert default_delegable(method, "/api/v2/" + path, {}), (method, path)

    class Caller:
        def __init__(self, kind):
            self.kind, self.delegated = kind, []

        def get(self, path, /, **query):
            return {"kind": self.kind} if path == "me" else self.call("GET", path, query=query)

        def post(self, path, body=None, key=None):
            return self.call("POST", path, body, key)

        def delete(self, path, key=None):
            return self.call("DELETE", path, key=key)

        def call(self, method, path, body=None, key=None, query=None, delegate=False):
            self.delegated.append(bool(delegate))
            return {}

    for name, entry in hubtools.BY_NAME.items():
        if not name.startswith("hub_peer_"):
            continue
        args = {field: "x" for field in entry["inputSchema"].get("required", [])}
        botops, bot = Caller("botops"), Caller("bot")
        entry["fn"](botops, dict(args))
        entry["fn"](bot, dict(args))
        assert botops.delegated and all(botops.delegated), name
        assert bot.delegated and not any(bot.delegated), name


# ----------------------------------------------------------------------------- a send that does not go
def deliveries(side, thread):
    return rows(side, "SELECT state FROM message_deliveries WHERE root_id=? AND recipient LIKE 'peer:%' ORDER BY created_at",
                (thread,))


def hq_answers(hq, monkeypatch, peer, status_code, error):
    """HQ answers every send to `peer` with this until the returned switch is turned off."""
    real, on = hq.envelope, {"on": True}

    def envelope(me, request):
        if on["on"] and request.headers.get("x-tico-to") == peer.id:
            return httpx.Response(status_code, json={"error": error})
        return real(me, request)

    monkeypatch.setattr(hq, "envelope", envelope)
    return on


def test_a_homes_delivery_is_delivered_only_once_hq_has_taken_it(make_side, hq, monkeypatch, clock):
    a, b = make_side("alpha"), make_side("beta")
    connect(a, b, hq)
    tid = make_thread(a, "Launch")
    share(a, tid, b)
    drive(a, b)
    busy = hq_answers(hq, monkeypatch, b, 503, "unavailable")
    say(a, tid, "first")
    tick(a)
    assert deliveries(a, tid) == [("pending",)]
    busy["on"] = False
    clock["t"] += 600
    tick(a)
    assert deliveries(a, tid) == [("delivered",)]


def test_a_refused_send_is_one_needs_you_item_until_retry_sends_it(make_side, hq, monkeypatch, clock):
    a, b = make_side("alpha"), make_side("beta")
    connect(a, b, hq)
    tid = make_thread(a, "Launch")
    share(a, tid, b)
    drive(a, b)
    refusing = hq_answers(hq, monkeypatch, b, 422, "refused")
    say(a, tid, "first")
    say(a, tid, "second")
    drive(a, rounds=3)
    assert set(deliveries(a, tid)) == {("failed",)}
    assert needs_kinds(a).count("thread_unsent") == 1          # one per thread and company, not one per message
    assert needs_kinds(a, BEN).count("thread_unsent") == 0     # for whoever manages the thread
    entry = next(t for t in status(a)["threads"] if t["thread_id"] == tid)
    assert entry["unsent"] == {b.id: 2}
    refusing["on"] = False
    retried = a.client.post(f"/api/v2/federation/threads/{tid}/installs/{b.id}/retry", headers=OWNER)
    assert retried.status_code == 200 and retried.json()["retried"] == 2
    assert needs_kinds(a).count("thread_unsent") == 0
    drive(a, b)
    assert texts(b, local_thread(b, tid, a)) == ["first", "second"]
    assert set(deliveries(a, tid)) == {("delivered",)}


def test_sends_that_keep_failing_are_listed_and_a_pause_is_not_a_failure(make_side, hq, monkeypatch, clock):
    a, b = make_side("alpha"), make_side("beta")
    connect(a, b, hq)
    tid = make_thread(a, "Launch")
    share(a, tid, b)
    drive(a, b)
    busy = hq_answers(hq, monkeypatch, b, 503, "unavailable")
    say(a, tid, "first")
    for _ in range(7):
        tick(a)
        clock["t"] += 600
    assert "thread_unsent" not in needs_kinds(a)
    tick(a)
    assert needs_kinds(a).count("thread_unsent") == 1          # still waiting, and somebody knows
    assert a.client.post(f"/api/v2/federation/peerings/{row_for(a, b.id)}/pause", json={"paused": True},
                         headers=OWNER).status_code == 200
    assert "thread_unsent" not in needs_kinds(a)               # a paused peering waits: nothing to do about the send
    assert a.client.post(f"/api/v2/federation/peerings/{row_for(a, b.id)}/pause", json={"paused": False},
                         headers=OWNER).status_code == 200
    busy["on"] = False
    clock["t"] += 600
    drive(a, b)
    assert "thread_unsent" not in needs_kinds(a) and texts(b, local_thread(b, tid, a)) == ["first"]


def test_unsharing_clears_what_was_not_sent(make_side, hq, monkeypatch):
    a, b = make_side("alpha"), make_side("beta")
    connect(a, b, hq)
    tid = make_thread(a, "Launch")
    share(a, tid, b)
    drive(a, b)
    hq_answers(hq, monkeypatch, b, 422, "refused")
    say(a, tid, "first")
    drive(a, rounds=2)
    assert needs_kinds(a).count("thread_unsent") == 1
    assert a.client.delete(f"/api/v2/federation/threads/{tid}/installs/{b.id}", headers=OWNER).status_code == 200
    assert "thread_unsent" not in needs_kinds(a)
