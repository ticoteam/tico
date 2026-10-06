"""Live events (backend/events.py): one stream of tasks, messages, runs, bots and Needs you, each sent
as it commits and only to people who may read it; resumable, reset when too far behind, and ended
when sign-in lapses. Against a real listening server, so delivery is the browser's."""

import http.client
import json
import queue
import socket
import sqlite3
import threading
import time
from urllib.parse import urlparse

import pytest
import uvicorn

from backend import events as E
from backend import hubdb as H
from backend.store import Problem
from backend.tests.test_api import api, headers, post, restrict, setup_attempt  # noqa: F401


@pytest.fixture
def live(api, monkeypatch):
    # Short keepalives, so a closed client is noticed (and the server can stop) within a second.
    monkeypatch.setattr(E, "KEEPALIVE_SECONDS", 0.5)
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(api.app, log_level="error"))
    thread = threading.Thread(target=server.run, kwargs={"sockets": [sock]}, daemon=True)
    thread.start()
    deadline = time.monotonic() + 5
    while not server.started and thread.is_alive() and time.monotonic() < deadline:
        time.sleep(0.01)
    assert server.started
    streams = []
    yield lambda token="ana-test", query="", headers=None: streams.append(
        Stream(f"http://127.0.0.1:{port}", token, query, headers)) or streams[-1]
    for stream in streams:
        stream.close()
    server.should_exit = True
    thread.join(timeout=10)
    sock.close()
    assert not thread.is_alive()


class Stream:
    """An open `GET /api/v2/events`, read on a thread into a queue of {event, id, data}."""

    def __init__(self, base, token, query="", headers=None):
        url = urlparse(base)
        self.conn = http.client.HTTPConnection(url.hostname, url.port, timeout=30)
        self.conn.request("GET", "/api/v2/events" + query,
                          headers={"Authorization": "Bearer " + token, **(headers or {})})
        self.response = self.conn.getresponse()
        self.status = self.response.status
        self.events = queue.Queue()
        self.seen = []
        if self.status == 200:
            threading.Thread(target=self._read, daemon=True).start()
        else:
            self.body = json.loads(self.response.read() or b"{}")

    def _read(self):
        event = {}
        try:
            for raw in self.response:
                line = raw.decode().rstrip("\n")
                if not line:
                    if event.get("event"):
                        self.events.put(event)
                    event = {}
                elif line.startswith(":"):
                    self.events.put({"event": "keepalive"})
                else:
                    name, _, value = line.partition(": ")
                    event[name] = json.loads(value) if name == "data" else value
        except (OSError, ValueError, http.client.HTTPException):
            pass
        self.events.put({"event": "closed"})

    def until(self, match, timeout=5.0):
        """The first event `match` accepts; every event read on the way is kept in `seen`."""
        deadline = time.monotonic() + timeout
        while True:
            left = deadline - time.monotonic()
            if left <= 0:
                raise AssertionError("no matching event; saw " + json.dumps([e.get("event") for e in self.seen]))
            try:
                event = self.events.get(timeout=left)
            except queue.Empty:
                continue
            self.seen.append(event)
            if match(event):
                return event
            if event["event"] == "closed":
                raise AssertionError("stream closed; saw " + json.dumps([e.get("event") for e in self.seen]))

    def drain(self, seconds=1.0):
        """Every event that arrives in the next `seconds`."""
        out, deadline = [], time.monotonic() + seconds
        while (left := deadline - time.monotonic()) > 0:
            try:
                out.append(self.events.get(timeout=left))
            except queue.Empty:
                break
        self.seen += out
        return out

    def close(self):
        try:
            self.response.fp.raw._sock.shutdown(socket.SHUT_RDWR)
        except (AttributeError, OSError):
            pass
        self.conn.close()


def topic(name, **fields):
    return lambda e: e["event"] == name and all(e["data"].get(k) == v for k, v in fields.items())


def task_event(task_id):
    return lambda e: e["event"] == "tasks" and e["data"]["id"] == task_id


def ready(stream):
    return stream.until(topic("ready"))["data"]["seq"]


@pytest.mark.slow
def test_a_task_change_reaches_another_person_signed_and_never_a_non_party(api, live):
    ana, cara = live(), live("cara-test")
    ready(ana), ready(cara)
    task = post(api, "tasks", {"owner": "ben", "title": "Review the launch copy", "body": "Please."})
    secret = post(api, "tasks", {"owner": "ben", "title": "Review the salary bands", "body": "Please.", "private": True})
    moved = post(api, "tasks/" + task["id"], {"version": task["version"], "status": "doing"}, token="ben-test")
    seen = cara.until(lambda e: e["event"] == "tasks" and e["data"]["id"] == task["id"]
                      and e["data"]["task"]["status"] == "doing")
    assert seen["data"]["task"]["version"] == moved["version"]
    assert seen["data"]["actor"] == "human:ben" and int(seen["id"]) == seen["data"]["seq"]
    # The requester sees the private one; Cara never does, nor anything about it.
    ana.until(task_event(secret["id"]))
    cara.drain(1.0)
    assert secret["id"] not in json.dumps(cara.seen)


@pytest.mark.slow
def test_resume_from_after_or_last_event_id_sends_only_what_came_since(api, live):
    first = post(api, "tasks", {"owner": "ben", "title": "Draft the agenda", "body": "Please."})
    with api.app.state.store.read() as c:
        cursor = E.latest(c)
    second = post(api, "tasks", {"owner": "ben", "title": "Book the room", "body": "Please."})
    for stream in (live("ben-test", f"?topics=tasks&after={cursor}"),
                   live("ben-test", "?topics=tasks&after=0", {"Last-Event-ID": str(cursor)})):
        got = stream.until(task_event(second["id"]))
        assert got["data"]["task"]["title"] == "Book the room"
        assert first["id"] not in json.dumps(stream.seen)


@pytest.mark.slow
def test_a_client_further_behind_than_the_log_keeps_is_told_to_reset(api, live):
    post(api, "tasks", {"owner": "ben", "title": "Clear the old work", "body": "Please."})
    with api.app.state.store.read() as c:
        old = E.latest(c)
    assert E.sweep(api.app.state.store, H.shift(H.now(), hours=E.KEEP_HOURS + 1)) > 0
    post(api, "tasks", {"owner": "ben", "title": "Plan the new work", "body": "Please."})
    stream = live("ben-test", "?after=1")
    reset = stream.until(topic("reset"))
    assert reset["data"]["seq"] > old
    # A cursor ahead of the log (another server's, or a restored database) resets too.
    assert live("ben-test", "?after=999999").until(topic("reset"))


@pytest.mark.slow
def test_a_stream_ends_with_expired_when_sign_in_lapses_and_ends_on_its_own(api, live, monkeypatch):
    stream = live("ben-test")
    ready(stream)
    real = api.app.state.auth.authenticate

    def lapsed(headers, *args, **kwargs):
        if headers.get("authorization") == "Bearer ben-test":
            raise Problem("unauthenticated", "Sign in again", 401)
        return real(headers, *args, **kwargs)
    monkeypatch.setattr(api.app.state.auth, "authenticate", lapsed)
    # Re-checked at the next read (or keepalive), whichever comes first.
    post(api, "tasks", {"owner": "ben", "title": "After sign-out", "body": "Please."})
    stream.until(topic("expired"))
    assert "After sign-out" not in json.dumps(stream.seen)
    monkeypatch.setattr(api.app.state.auth, "authenticate", real)
    monkeypatch.setattr(E, "LIFETIME_SECONDS", 0.3)
    short = live("ben-test")
    short.until(lambda e: e["event"] == "closed", timeout=5)


def test_events_are_for_people(api):
    r, _, attempt = setup_attempt(api)
    assert api.get("/api/v2/events", headers=headers(attempt["token"])).status_code == 403
    assert api.get("/api/v2/events", headers=headers(r["token"])).status_code == 403
    assert api.get("/api/v2/events?topics=tasks,gossip", headers=headers()).status_code == 422


@pytest.mark.slow
def test_a_runs_output_and_state_reach_the_room_and_respect_bot_read_access(api, live):
    ana = live("ana-test", "?topics=runs,messages")
    ready(ana)
    r, msg, attempt = setup_attempt(api)
    aid = attempt["id"]
    post(api, f"attempts/{aid}/started", {"thread_id": "t1"}, token=r["token"])
    post(api, f"attempts/{aid}/events", {"events": [{"seq": 1, "kind": "delta",
                                                       "payload": {"text": "Working on it", "delta_kind": "text"}}]},
         token=r["token"])
    out = ana.until(lambda e: e["event"] == "runs" and "output" in e["data"])
    assert out["data"]["attempt_id"] == aid and out["data"]["conversation_id"] == msg["conversation_id"]
    assert out["data"]["output"]["payload"]["text"] == "Working on it" and out["data"]["bot"] == "ops"
    states = [e["data"]["state"] for e in ana.seen if e["event"] == "runs" and "state" in e["data"]]
    assert "running" in states, states
    # Ana's room with ops is hers: Cara gets nothing of it, and with ops's activity closed to her,
    # nothing narrowed to ops either.
    with api.app.state.store.transaction() as c:
        restrict(c, "ops", people=["ana"])
    cara = live("cara-test", "?topics=runs&bot=ops")
    ready(cara)
    post(api, f"attempts/{aid}/events", {"events": [{"seq": 2, "kind": "delta",
                                                       "payload": {"text": "Still going", "delta_kind": "text"}}]},
         token=r["token"])
    ana.until(lambda e: e["event"] == "runs" and (e["data"].get("output") or {}).get("seq") == 2)
    cara.drain(1.0)
    assert "Still going" not in json.dumps(cara.seen)


def test_every_source_writes_its_topic(api):
    """Trigger coverage: each table a page follows logs the topic it belongs to."""
    store = api.app.state.store
    r, msg, attempt = setup_attempt(api)

    def logged(write):
        with store.transaction() as c:
            before = E.latest(c)
            write(c)
            return {(row["topic"], row["kind"]) for row in c.execute("SELECT * FROM changes WHERE seq>?", (before,))}
    aid, cid = attempt["id"], msg["conversation_id"]
    approval = post(api, "approvals", {"kind": "send", "payload": {"to": "x@acme.example", "cc": [], "subject": "Hi",
                                                                   "body_sha256": "a" * 64, "mailbox": "work"}},
                    token=attempt["token"])
    task = post(api, "tasks", {"owner": "ben", "title": "Coverage", "body": "Please."})
    assert logged(lambda c: c.execute("UPDATE tasks SET title='Covered' WHERE id=?", (task["id"],))) == {
        ("tasks", "update"), ("needs", "task")}
    assert logged(lambda c: c.execute("INSERT INTO task_links(id,task_id,kind,url,created) VALUES('l1',?,'pr','u',?)",
                                      (task["id"], H.now()))) == {("tasks", "link")}
    assert ("messages", "update") in logged(lambda c: c.execute("UPDATE messages SET body='edited' WHERE id=?", (msg["id"],)))
    assert logged(lambda c: c.execute("UPDATE messages SET read_at=? WHERE id=?", (H.now(), msg["id"]))) == set()
    assert logged(lambda c: c.execute("INSERT INTO attempt_events(attempt_id,seq,kind,payload_json,created) "
                                      "VALUES(?,99,'delta','{}',?)", (aid, H.now()))) == {("runs", "output")}
    assert logged(lambda c: c.execute("UPDATE attempts SET lease_until=? WHERE id=?", (H.now(), aid))) == set()
    assert logged(lambda c: c.execute("UPDATE attempts SET state='running' WHERE id=?", (aid,))) == {("runs", "attempt")}
    assert logged(lambda c: c.execute("UPDATE jobs SET state='completed' WHERE attempt_id=?", (aid,))) == {("runs", "job")}
    assert logged(lambda c: c.execute("INSERT INTO bot_status(bot,state) VALUES('finance','idle')")) == {("bots", "status")}
    assert logged(lambda c: c.execute("UPDATE bot_status SET updated_at='x' WHERE bot='finance'")) == set()
    assert logged(lambda c: c.execute("UPDATE bots SET state='paused' WHERE slug='finance'")) == {("bots", "state")}
    assert logged(lambda c: c.execute("INSERT INTO bot_control(bot,draining) VALUES('finance',1)")) == {("bots", "control")}
    assert logged(lambda c: c.execute("UPDATE approvals SET decision='approved' WHERE id=?",
                                      (approval["id"],))) == {("needs", "approval")}
    assert logged(lambda c: c.execute("INSERT INTO chat_goals(id,conversation_id,bot,objective,status,set_by,set_at,updated_at) "
                                      "VALUES('g1',?,'ops','Ship it','active','human:ana',?,?)",
                                      (cid, H.now(), H.now()))) == {("messages", "goal")}
    loose = post(api, "tasks", {"owner": "ben", "title": "Remove this", "body": "Please."})
    with store.read() as c:
        before = E.latest(c)
    post(api, "tasks/" + loose["id"] + "/delete", {})
    with store.read() as c:
        assert ("tasks", "delete") in {(row["topic"], row["kind"]) for row in c.execute("SELECT * FROM changes WHERE seq>?", (before,))}
    # Every trigger defined is installed, so a source cannot quietly drop out.
    with store.read() as c:
        installed = {row[0] for row in c.execute("SELECT name FROM sqlite_master WHERE type='trigger' AND name LIKE 'changes_%'")}
    assert installed == {name for name, *_ in E.TRIGGERS}


def test_the_log_is_installed_idempotently_and_follows_its_definitions(api, monkeypatch):
    store = api.app.state.store
    with store.transaction() as c:
        E.ensure(c)
        E.ensure(c)
        before = c.execute("SELECT sql FROM sqlite_master WHERE name='changes_bots_update'").fetchone()[0]
    # A changed definition replaces the installed trigger at the next start; one removed goes.
    monkeypatch.setattr(E, "TRIGGERS", [t if t[0] != "changes_bots_update" else (*t[:3], "1", t[4]) for t in E.TRIGGERS
                                        if t[0] != "changes_bot_control_delete"])
    with store.transaction() as c:
        E.ensure(c)
        after = c.execute("SELECT sql FROM sqlite_master WHERE name='changes_bots_update'").fetchone()[0]
        assert after != before and " WHEN 1 " in after
        assert not c.execute("SELECT 1 FROM sqlite_master WHERE name='changes_bot_control_delete'").fetchone()
    # A table that is not there (yet) is skipped, not an error.
    conn = sqlite3.connect(":memory:")
    E.ensure(conn)
    assert conn.execute("SELECT count(*) FROM sqlite_master WHERE type='trigger'").fetchone()[0] == 0
