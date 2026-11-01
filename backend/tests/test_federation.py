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
from backend import gateway_connection as GC
from backend import hook_relay
from backend import hubdb as H
from backend import org_rule_check as OR
from backend import threads as CORE
from backend.app import create_app
from backend.auth import Identity
from backend.config import Settings
from backend.store import Problem, encode

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

    def make(name):
        root = tmp_path / name
        registry = root / "registry"
        registry.mkdir(parents=True)
        (registry / "hub-access.yaml").write_text(yaml.safe_dump({"owner": "ana@acme.example", "bot_admins": []}))
        settings = Settings(db_path=root / "hub.db", registry_dir=registry, gateway_key="tgw_" + name, company_name=name.title() + " Co", test_identities={
            "ana-test": Identity("human:ana", "owner", "ana@acme.example"),
            "ben-test": Identity("human:ben", "human", "ben@acme.example")})
        app = create_app(settings)
        client = stack.enter_context(TestClient(app))          # the lifespan creates the schema
        install = "gi_" + hashlib.sha256(name.encode()).hexdigest()[:24]
        with app.state.store.transaction() as c:
            GC._write(c, {"install": install})
            H.sync_registry(c, {}, PEOPLE)                    # the humans rows sign-in reads
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
    with side.app.state.store.transaction() as c:
        return CORE.create(c, "keeper", title=title, audience="members")["id"]


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


def test_only_the_owner_manages_federation(make_side, hq):
    a = make_side("alpha")
    assert a.client.get("/api/v2/federation", headers={"Authorization": "Bearer ben-test"}).status_code == 403


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
    assert texts(b, there) == ["first, edited"]
    with b.app.state.store.read() as c:
        assert c.execute("SELECT catch_up_requested_at FROM federation_threads WHERE thread_id=?",
                         (there,)).fetchone()[0] is not None


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


def test_the_kill_switch_stops_both_directions_and_resume_repairs(make_side, hq):
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
    assert guests() == ["human:ben@" + a.id]
    with a.app.state.store.transaction() as c:
        CORE.remove_member(c, "keeper", tid, "human:ben")
    drive(a, b, rounds=3)
    assert guests() == []


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
