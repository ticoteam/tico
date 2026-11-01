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
from backend.auth import Identity
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
    deadline = time.monotonic() + 30
    while not server.started and thread.is_alive() and time.monotonic() < deadline:
        time.sleep(0.01)
    assert server.started, "test API did not become ready within 30 seconds"
    streams = []
    yield lambda token="ana-test", query="", headers=None, path="/api/v2/events": streams.append(
        Stream(f"http://127.0.0.1:{port}", token, query, headers, path)) or streams[-1]
    for stream in streams:
        stream.close()
    server.should_exit = True
    thread.join(timeout=10)
    sock.close()
    assert not thread.is_alive()


class Stream:
    """An open `GET /api/v2/events`, read on a thread into a queue of {event, id, data}."""

    def __init__(self, base, token, query="", headers=None, path="/api/v2/events"):
        url = urlparse(base)
        self.conn = http.client.HTTPConnection(url.hostname, url.port, timeout=30)
        self.conn.request("GET", path + query,
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

    def until(self, match, timeout=30.0):   # a deadline, not a wait: a busy computer can take seconds per event
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
    task = post(api, "tasks", {"owner_id": "ben", "title": "Review the launch copy", "body": "Please."})
    secret = post(api, "tasks", {"owner_id": "ben", "title": "Review the salary bands", "body": "Please.", "private": True})
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
    first = post(api, "tasks", {"owner_id": "ben", "title": "Draft the agenda", "body": "Please."})
    with api.app.state.store.read() as c:
        cursor = E.latest(c)
    second = post(api, "tasks", {"owner_id": "ben", "title": "Book the room", "body": "Please."})
    for stream in (live("ben-test", f"?topics=tasks&after={cursor}"),
                   live("ben-test", "?topics=tasks&after=0", {"Last-Event-ID": str(cursor)})):
        got = stream.until(task_event(second["id"]))
        assert got["data"]["task"]["title"] == "Book the room"
        assert first["id"] not in json.dumps(stream.seen)


@pytest.mark.slow
def test_task_and_party_send_only_those_tasks_and_a_partys_including_one_moved_away(api, live):
    with api.app.state.store.read() as c:
        cursor = E.latest(c)
    bens = post(api, "tasks", {"owner_id": "ben", "title": "Draft the agenda", "body": "Please."})
    caras = post(api, "tasks", {"owner_id": "cara", "title": "Book the room", "body": "Please."})
    handed = post(api, "tasks", {"owner_id": "ben", "title": "Order the lunch", "body": "Please."})
    handed = post(api, "tasks/" + handed["id"], {"version": handed["version"], "owner_id": "cara"})
    last = post(api, "tasks", {"owner_id": "cara", "title": "Print the badges", "body": "Please."})
    party = live("ana-test", f"?topics=tasks&after={cursor}&party=human:ben")
    party.until(lambda e: e["event"] == "tasks" and e["data"]["id"] == handed["id"] and e["data"]["task"]["owner_id"] == "human:cara")
    party.drain(1.0)
    sent = {e["data"]["id"] for e in party.seen if e["event"] == "tasks"}
    assert sent == {bens["id"], handed["id"]}
    one = live("ana-test", f"?topics=tasks&after={cursor}&task={caras['id']}")
    one.until(task_event(caras["id"]))
    one.drain(1.0)
    assert {e["data"]["id"] for e in one.seen if e["event"] == "tasks"} == {caras["id"]}
    assert last["id"] not in json.dumps(one.seen)


@pytest.mark.slow
def test_a_client_further_behind_than_the_log_keeps_is_told_to_reset(api, live):
    post(api, "tasks", {"owner_id": "ben", "title": "Clear the old work", "body": "Please."})
    with api.app.state.store.read() as c:
        old = E.latest(c)
    assert E.sweep(api.app.state.store, H.shift(H.now(), hours=E.KEEP_HOURS + 1)) > 0
    post(api, "tasks", {"owner_id": "ben", "title": "Plan the new work", "body": "Please."})
    stream = live("ben-test", "?after=1")
    reset = stream.until(topic("reset"))
    assert reset["data"]["seq"] > old
    # A cursor ahead of the log (another server's, or a restored database) resets too.
    assert live("ben-test", "?after=999999").until(topic("reset"))


@pytest.mark.slow
def test_a_stream_ends_with_expired_when_sign_in_lapses_and_ends_on_its_own(api, live, monkeypatch):
    monkeypatch.setattr(E, "SIGN_IN_RECHECK_SECONDS", 0)     # every poll checks, so the lapse ends it before its data
    stream = live("ben-test")
    ready(stream)
    real = api.app.state.auth.authenticate

    def lapsed(headers, *args, **kwargs):
        if headers.get("authorization") == "Bearer ben-test":
            raise Problem("unauthenticated", "Sign in again", 401)
        return real(headers, *args, **kwargs)
    monkeypatch.setattr(api.app.state.auth, "authenticate", lapsed)
    # Re-checked at the next read (or keepalive), whichever comes first.
    post(api, "tasks", {"owner_id": "ben", "title": "After sign-out", "body": "Please."})
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
    assert logged(lambda c: c.execute("INSERT INTO task_links(id,task_id,kind,url,created_at) VALUES('l1',?,'pr','u',?)",
                                      (task["id"], H.now()))) == {("tasks", "link")}
    # What a task's row shows besides its own columns: its tags, and a tag's own name.
    with store.transaction() as c:
        c.execute("INSERT INTO tags(id,key,label,created_at,updated_at) VALUES('tg1','coverage','Coverage',?,?)", (H.now(), H.now()))
    assert logged(lambda c: c.execute("INSERT INTO task_tags(task_id,tag_id) VALUES(?,'tg1')",
                                      (task["id"],))) == {("tasks", "tags")}
    assert logged(lambda c: c.execute("UPDATE tags SET label='Covered' WHERE id='tg1'")) == {("tasks", "tags")}
    assert ("messages", "update") in logged(lambda c: c.execute("UPDATE messages SET body='edited' WHERE id=?", (msg["id"],)))
    assert logged(lambda c: c.execute("UPDATE messages SET read_at=? WHERE id=?", (H.now(), msg["id"]))) == set()
    assert logged(lambda c: c.execute("INSERT INTO attempt_events(attempt_id,seq,kind,payload,created_at) "
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


def test_a_task_link_rewritten_with_the_same_values_logs_nothing_and_a_change_to_any_column_logs_once(api):
    """The link triggers fire on a real change to any column (read from the table), never on a rewrite with the same values:
    a worktree heartbeat rewrote closed tasks' links every few minutes, and each write rebuilt every open board."""
    store = api.app.state.store
    setup_attempt(api)
    task = post(api, "tasks", {"owner_id": "ben", "title": "Review the links", "body": "Please."})
    with store.transaction() as c:
        c.execute("INSERT INTO task_links(id,task_id,kind,url,state,computer_id,created_at) "
                  "VALUES('wt1',?,'worktree','u','present','rc1',?)", (task["id"], H.now()))

    def logged(sql):
        with store.transaction() as c:
            before = E.latest(c)
            c.execute(sql)
            return {(row["topic"], row["kind"]) for row in c.execute("SELECT * FROM changes WHERE seq>?", (before,))}
    with store.read() as c:
        columns = [row[1] for row in c.execute("PRAGMA table_info(task_links)")]
    assert {"repo", "detail", "computer_id", "pr_merged_at"} <= set(columns)
    for column in columns:
        assert logged(f"UPDATE task_links SET {column}={column} WHERE id='wt1'") == set(), column
    assert logged("UPDATE task_links SET state='unknown' WHERE id='wt1'") == {("tasks", "link"), ("runner", "worktrees")}
    for column in columns:
        if column in ("id", "task_id"):
            continue
        assert ("tasks", "link") in logged(f"UPDATE task_links SET {column}='x{column}' WHERE id='wt1'"), column
    # A pull request link is no worktree: the task still hears of it, the computers do not.
    with store.transaction() as c:
        c.execute("INSERT INTO task_links(id,task_id,kind,url,created_at) VALUES('pr1',?,'pr','https://github.com/acme/app/pull/9',?)",
                  (task["id"], H.now()))
    assert logged("UPDATE task_links SET title='Renamed' WHERE id='pr1'") == {("tasks", "link")}


def test_a_ring_after_a_quiet_window_is_read_at_once_and_a_burst_still_once_a_window(monkeypatch):
    monkeypatch.setattr(E.time, "monotonic", lambda: 100.0)
    assert E.gathered(0.25, read_at=0.0) == E.LEAD_SECONDS          # quiet: read almost at once
    assert abs(E.gathered(0.25, read_at=99.9) - 0.15) < 1e-9        # just read: only the rest of the window


class CountingAuth:
    """Stands in for Auth.authenticate: counts calls and answers with `role`, which a test may change."""

    def __init__(self, role="human"):
        self.calls, self.role = 0, role

    def authenticate(self, headers):
        self.calls += 1
        return Identity("human:ben", self.role)


def test_a_streams_sign_in_is_checked_once_across_a_burst_of_polls_and_again_after_the_window():
    now, auth = [100.0], CountingAuth()
    check = E.SignIn(auth, {"authorization": "Bearer ben-test"}, Identity("human:ben", "human"), clock=lambda: now[0])
    for _ in range(50):                    # polls and keepalives that come within the window
        check()
    assert auth.calls == 1
    now[0] += E.SIGN_IN_RECHECK_SECONDS - 0.1
    check()
    assert auth.calls == 1
    now[0] += 0.1
    check()
    assert auth.calls == 2


def test_a_changed_role_or_a_refused_sign_in_ends_the_stream_and_is_never_remembered():
    now, auth = [0.0], CountingAuth(role="owner")
    check = E.SignIn(auth, {}, Identity("human:ben", "human"), clock=lambda: now[0])
    with pytest.raises(Problem) as refused:
        check()
    assert refused.value.status == 401 and auth.calls == 1
    # The refusal is not kept: the very next check asks again, and a sign-in that is back in place passes.
    auth.role = "human"
    check()
    assert auth.calls == 2


def test_a_read_with_tasks_and_bots_asks_for_the_viewers_bot_access_once(api, monkeypatch):
    """Task visibility and the bot lines of one read share the viewer's bot access, rather than each asking for it."""
    auth, store = api.app.state.auth, api.app.state.store
    with store.read() as c:
        cursor = E.latest(c)
    post(api, "tasks", {"owner_id": "ben", "title": "Count the asks", "body": "Please."})
    with store.transaction() as c:
        c.execute("UPDATE bots SET state='paused' WHERE slug='ops'")
    real, asked = auth.bot_accesses, []
    monkeypatch.setattr(auth, "bot_accesses", lambda c, who, slugs=None: asked.append(slugs) or real(c, who, slugs))
    who = Identity("human:ben", "human", "ben@acme.example")
    with store.read() as c:
        events, _, _, _ = E.read(c, auth, who, cursor, topics=("tasks", "bots"),
                                 task_views=lambda rows, c, who, visible_sql="1": [], task_view=None)
    assert [topic for _, topic, _ in events] == ["bots"]
    assert len(asked) == 1

