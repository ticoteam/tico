#!/usr/bin/env python3
"""Thread write and live-event timings (docs/dev/threads-contract.md §9) on owned loopback services, fictional data.

Targets: a write's p95 under 150 ms, and an event's p95 under 1 s from the writer's send to the viewer's read. One run
holds, for --seconds: --viewers live streams on one room (GET /api/v2/events?thread=), --writers humans posting to it
at --rate messages a second each (POST /api/v2/threads/{id}/messages), one writer on a thread with --followers
followers, a --burst of clients reconnecting together halfway through, and one slow consumer that never reads.

--preset names a scenario: `default` (the flags' defaults: 20 writers at 5 a second, 100 offered writes a second) or
`realistic` (20 writers at 1 a second, 20 writes a second, with the same 50 viewers and 200-client burst), whose
targets are a write p95 of 150 ms, an event p95 of 1 s and a burst ready p95 of 2 s. Flags given after it override it.

--path conversation runs the same load on the write path that predates threads: a direct conversation, streams with
?conversation= and POST /api/v2/conversations/{id}/messages. It is the baseline, run on a commit without threads.

The server runs in a child process (--serve), so the load generator's threads never share its interpreter lock: the
latencies are the server's. BENCH_PROFILE=1 makes the server time its write and fan-out functions and print the totals
when the run ends; BENCH_SAMPLE=<file> (Python 3.15 and later) writes the server's stacks, sampled while they hold the
interpreter lock, to <file> in collapsed form. Writer and viewers share one clock, so latencies are exact to the scheduler, not a network estimate.
Run it on a clean VM with scripts/remote.py, not on a laptop, and keep the output with the commit it measured.
"""

import argparse
import contextlib
import functools
import http.client
import importlib
import json
import multiprocessing
import os
import platform
import re
import socket
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import conftest  # noqa: E402,F401  isolates the registry and AWS credentials, as the contract tests do
import uvicorn  # noqa: E402

from backend import hubdb as H  # noqa: E402
from backend.tests import test_api  # noqa: E402

WRITE_P95_MS, EVENT_P95_S, BURST_P95_S = 150, 1.0, 2.0
PRESETS = {"default": {}, "realistic": {"viewers": 50, "writers": 20, "rate": 1.0, "burst": 200}}
SENT = re.compile(r"sent=([0-9.]+)")
TOKENS = ["ana-test", "ben-test", "cara-test"]          # the fixture's people; members of both threads
DM_TOKENS = ["ana-test", "ben-test"]                    # the two people of the baseline's direct conversation
# What the server times when BENCH_PROFILE=1: the write path, its fan-out and the stream's reads.
PRIVATE_TAG = "private-only-"     # in every message of --identities' private thread
SLOW_READ_SECONDS = 30     # how long the slow consumer may take over its backlog once it reads
PROFILED = (("backend.threads", ("post", "deliver", "mentions", "audience_has", "_followers", "_insert_delivery",
                                 "_follow", "wake_mode", "thread_row", "can_post", "denial", "_outside", "view",
                                 "message_readable", "readable_ids")),
            ("backend.events", ("read_in", "Rounds._round", "_Snapshot.__init__", "read", "latest", "oldest", "_Viewer.__init__", "_Viewer.prime", "_Viewer.conversation",
                                "_Viewer.bot_readable", "_messages", "_threads", "_bots", "_tasks", "_needs_list",
                                "_change_rows", "_stored_messages", "encode")),
            ("backend.store", ("Store.connect",)),
            ("backend.message_links", ("refresh_dirty",)),
            ("backend.org_rule_check", ("in_force", "apply", "preflight")))


def percentile(values, share):
    values = sorted(values)
    return values[int(share * (len(values) - 1))] if values else None


def summary(values):
    return {"n": len(values), "p50": percentile(values, .5), "p95": percentile(values, .95),
            "max": max(values) if values else None}


class Profile:
    """Wall time per call of the functions in PROFILED, and of Store.transaction (its gate wait, body and commit)."""

    def __init__(self, store_module):
        self.calls, self.cpu = {}, {}
        for module_name, names in PROFILED:
            try:
                module = importlib.import_module(module_name)
            except ImportError:           # origin/main has no backend.threads: the baseline profiles what exists
                continue
            for name in names:
                owner, attr = module, name
                if "." in name:                   # Class.method
                    owner = getattr(module, name.split(".", 1)[0], None)
                    attr = name.split(".", 1)[1]
                original = getattr(owner, attr, None) if owner is not None else None
                if original is not None:
                    label = f"{module_name.rsplit('.', 1)[-1]}.{name}"
                    setattr(owner, attr, self.wrap(label, original))
        gate = getattr(store_module, "WriterGate", None)
        if gate is not None:                  # the in-process write queue: time waiting for it, apart from the hold
            gate.acquire = self.wrap("store.writer_gate_acquire", gate.acquire)
        original_transaction = store_module.Store.transaction
        recorded = self.calls.setdefault("store.transaction", [])

        @contextlib.contextmanager
        def transaction(store):
            began = time.perf_counter()
            try:
                with original_transaction(store) as c:
                    yield c
            finally:
                recorded.append((time.perf_counter() - began) * 1000)
        store_module.Store.transaction = transaction

    def wrap(self, label, function):
        recorded = self.calls.setdefault(label, [])
        cpu_recorded = self.cpu.setdefault(label, [])

        @functools.wraps(function)
        def timed(*args, **kwargs):
            began, cpu_began = time.perf_counter(), time.thread_time()
            try:
                return function(*args, **kwargs)
            finally:
                recorded.append((time.perf_counter() - began) * 1000)
                cpu_recorded.append((time.thread_time() - cpu_began) * 1000)   # the CPU, not the wait for the GIL
        return timed

    def summary(self):
        out = {}
        for label, values in sorted(self.calls.items()):
            out[label] = {"n": len(values), "total_ms": round(sum(values), 1),
                          "mean_ms": round(sum(values) / len(values), 3) if values else None,
                          "p95_ms": round(percentile(values, .95), 3) if values else None,
                          "max_ms": round(max(values), 3) if values else None}
            cpu = self.cpu.get(label) or []
            if cpu:                                  # CPU time of the calling thread: what the call costs the server
                out[label].update({"cpu_total_ms": round(sum(cpu), 1), "cpu_mean_ms": round(sum(cpu) / len(cpu), 3)})
        return out


def boot(directory):
    """The test API's app, seeded as the tests seed it, on a loopback port: (generator, server, thread, port, app)."""
    generator = test_api.api.__wrapped__(Path(directory), None)
    client = next(generator)
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    server = uvicorn.Server(uvicorn.Config(client.app, log_level="error"))
    thread = threading.Thread(target=server.run, kwargs={"sockets": [sock]}, daemon=True)
    thread.start()
    deadline = time.monotonic() + 30
    while not server.started and time.monotonic() < deadline:
        time.sleep(0.01)
    if not server.started:
        raise SystemExit("the benchmark server did not start within 30 seconds")
    return generator, server, thread, sock.getsockname()[1], client.app


def serve():
    """The server alone, in this process: prints its port and database, serves until stdin closes, then prints the
    profile (when BENCH_PROFILE is set) as its last line."""
    for setting in filter(None, os.environ.get("BENCH_SET", "").split(",")):   # module.NAME=int, to tune a constant
        name, _, value = setting.partition("=")
        module, _, attr = name.rpartition(".")
        setattr(importlib.import_module(module), attr, type(getattr(importlib.import_module(module), attr))(value))
    with tempfile.TemporaryDirectory(prefix="threads-benchmark-") as directory:
        generator, server, thread, port, app = boot(directory)
        seed_identities(app, int(os.environ.get("BENCH_IDENTITIES") or 0))
        profile = Profile(sys.modules["backend.store"]) if os.environ.get("BENCH_PROFILE") else None
        print(json.dumps({"port": port, "db": str(app.state.store.settings.db_path)}), flush=True)
        sys.stdin.read()                                   # the load generator closes stdin when the run ends
        server.should_exit = True
        thread.join(10)
        generator.close()
        print(json.dumps({"profile": profile.summary() if profile else None}), flush=True)


def call(conn, method, path, token, payload=None):
    headers = {"Authorization": f"Bearer {token}"}
    body = None
    if method != "GET":
        headers["Idempotency-Key"] = uuid.uuid4().hex
    if payload is not None:
        body = json.dumps(payload)
        headers["Content-Type"] = "application/json"
    conn.request(method, path, body=body, headers=headers)
    response = conn.getresponse()
    return response.status, response.read()


VIEWER_GROUPS = ("room", "room", "private", "outsider", "everything")


def distinct_viewer(i):
    """--identities: viewer `i`'s token and actor (each third a bot on an agent token) and its group: a member following
    the room, a member following the private thread, an outsider following one of them (who must get nothing), or a
    member of the room with a stream of everything it may read (who must never get the private thread)."""
    name = f"viewer-{i:03d}"
    return f"{name}-test", ("bot:" if i % 3 == 2 else "human:") + name, VIEWER_GROUPS[i % len(VIEWER_GROUPS)]


def seed_identities(app, count):
    """The server's side of --identities: people on the roster and bots with agent tokens, signing in by test token."""
    from backend.auth import Identity
    store, settings = app.state.store, app.state.auth.settings
    with store.transaction() as c:
        roster = json.loads(c.execute("SELECT value_json FROM registry_metadata WHERE key='people'").fetchone()[0])
        for i in range(count):
            token, actor, _ = distinct_viewer(i)
            kind, name = actor.split(":")
            if kind == "bot":
                settings.test_identities[token] = Identity(actor, "bot", agent="test")
                c.execute("INSERT OR IGNORE INTO bots(slug,display_name,runtime,created) VALUES(?,?,'fake',?)",
                          (name, name, H.now()))
                c.execute("INSERT INTO agents(bot,harness,token_hash,created,created_by) VALUES(?,?,?,?,?)",
                          (name, "test", "x-" + name, H.now(), "human:ana"))
            else:
                settings.test_identities[token] = Identity(actor, "human", f"{name}@acme.example")
                c.execute("INSERT OR IGNORE INTO humans(id,name,email) VALUES(?,?,?)",
                          (name, name, f"{name}@acme.example"))
                roster["people"].append({"id": name, "email": f"{name}@acme.example"})
        c.execute("UPDATE registry_metadata SET value_json=? WHERE key='people'", (json.dumps(roster),))


def stream(port, token, query, state, started=None):
    """Read one live stream to its end: the ready time and seq, the latency of each of this run's messages, resets."""
    state.update({"ready": None, "ready_seq": None, "latencies": [], "ids": set(), "reset": False, "error": None,
                  "status": None, "private_seen": 0})
    try:
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=300)
        state["conn"] = conn
        opened = started or time.time()
        conn.request("GET", "/api/v2/events" + query, headers={"Authorization": f"Bearer {token}"})
        response = conn.getresponse()
        state["status"] = response.status
        event = {}
        for raw in response:
            line = raw.decode().rstrip("\n")
            if line.startswith(":"):
                continue
            if line:
                name, _, value = line.partition(": ")
                event[name] = value
                continue
            name = event.get("event")
            if name == "ready" and state["ready"] is None:
                state["ready"] = time.time() - opened
                state["ready_seq"] = json.loads(event.get("data") or "{}").get("seq")
            elif name == "messages":
                data = json.loads(event.get("data") or "{}")
                message = data.get("message") if isinstance(data.get("message"), dict) else data  # nested or flat
                body = str(message.get("body") or "")
                match = SENT.search(body)
                key = data.get("id") or message.get("id")
                state["private_seen"] += PRIVATE_TAG in body
                if match and key not in state["ids"]:
                    state["ids"].add(key)
                    state["latencies"].append(time.time() - float(match.group(1)))
            elif name == "reset":
                state["reset"] = True
            event = {}
    except (OSError, ValueError, http.client.HTTPException) as error:
        state["error"] = repr(error)


def slow_consumer(port, token, query, state, stop):
    """Opens a stream with a small receive buffer and reads nothing until the run ends, then reads what came, with an
    ordinary buffer so its backlog arrives in seconds, until `reset` or until it is hung up on (SLOW_READ_SECONDS)."""
    state.update({"status": None, "events_after_wait": 0, "reset": False, "closed": False, "error": None})
    try:
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=300)
        state["conn"] = conn
        # Loopback's 64 KB segments dwarf a small buffer: Linux would hold back the window update once reading starts,
        # and its next probe can be a minute away. Ordinary segments keep the wait the server's, not the kernel's.
        conn.sock = socket.socket()
        with contextlib.suppress(OSError):
            conn.sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_MAXSEG, 1400)
        conn.sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 4096)
        conn.sock.connect(("127.0.0.1", port))
        conn.request("GET", "/api/v2/events" + query, headers={"Authorization": f"Bearer {token}"})
        response = conn.getresponse()
        state["status"] = response.status
        stop.wait()
        conn.sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 1 << 20)
        for raw in response:
            line = raw.decode().rstrip("\n")
            if line.startswith("event: "):
                state["events_after_wait"] += 1
                state["last_event"] = line[7:]
                if line == "event: reset":
                    state["reset"] = True
                    break
        state["closed"] = True
    except (OSError, ValueError, http.client.HTTPException) as error:
        state["error"] = repr(error)


def writer(port, token, path, tag, seconds, rate, state, phase=0.0):
    """Posts at `rate` a second for `seconds`; each message carries its send time for the viewers to measure. `phase`
    (a share of the period) spreads the writers over it: people do not all press send at the same instant."""
    state.update({"writes": [], "ok": 0, "statuses": {}, "error": None})
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=60)
    period, end, next_at, n = 1 / rate, time.time() + seconds, time.time() + phase / rate, 0
    while time.time() < end:
        time.sleep(max(0, next_at - time.time()))
        next_at += period
        n += 1
        text = f"Benchmark message {tag}-{n} sent={time.time():.6f}"
        began = time.perf_counter()
        status, _ = call(conn, "POST", path, token, {"text": text})
        state["writes"].append((time.perf_counter() - began) * 1000)
        state["statuses"][status] = state["statuses"].get(status, 0) + 1
        state["ok"] += status == 200


def seed_followers(db, thread_id, count):
    """Followers for the big thread: thread_members and thread_followers rows, as a company's roster would make."""
    now = H.now()
    with contextlib.closing(sqlite3.connect(str(db), timeout=60)) as c, c:
        c.executemany("INSERT INTO thread_members(id,thread_id,actor,role,added_by,created_at,updated_at) "
                      "VALUES(?,?,?,?,?,?,?)", [(uuid.uuid4().hex, thread_id, f"human:member-{i:04d}", "member",
                                                 "human:ana", now, now) for i in range(count)])
        c.executemany("INSERT INTO thread_followers(id,thread_id,actor,following,notify,reason,last_read_seq,"
                      "created_at,updated_at) VALUES(?,?,?,1,'live','manual',0,?,?)",
                      [(uuid.uuid4().hex, thread_id, f"human:member-{i:04d}", now, now) for i in range(count)])


def db_counts(db):
    """Rows the run left behind: `changes` per topic (each row can wake every stream that reads it), and table counts.
    A table this branch does not have is left out."""
    out = {}
    with contextlib.closing(sqlite3.connect(str(db), timeout=60)) as c:
        for name, sql in (("changes_by_topic", "SELECT topic, COUNT(*) FROM changes GROUP BY topic"),
                          ("messages", "SELECT COUNT(*) FROM messages"),
                          ("message_deliveries", "SELECT COUNT(*) FROM message_deliveries"),
                          ("thread_followers", "SELECT COUNT(*) FROM thread_followers")):
            try:
                found = c.execute(sql).fetchall()
            except sqlite3.OperationalError:
                continue
            out[name] = dict(found) if name == "changes_by_topic" else found[0][0]
    return out


def hang_up(state):
    """Closes one stream's socket, which ends a read that is blocked on the server."""
    try:
        state["conn"].sock.shutdown(socket.SHUT_RDWR)
    except (AttributeError, OSError):
        pass


def start(target, *args):
    thread = threading.Thread(target=target, args=args, daemon=True)
    thread.start()
    return thread


class Readers:
    """The live streams, read in processes of their own: at a busy run they parse tens of thousands of events a second,
    and in the writers' process their interpreter lock would be counted as the server's write and event latency."""

    PROCESSES = 6
    FIELDS = ("ready", "ready_seq", "latencies", "reset", "error", "status", "private_seen")

    def __init__(self):
        context = multiprocessing.get_context("fork")     # before this process has threads
        self.pipes, self.count = [], 0
        for _ in range(self.PROCESSES):
            ours, theirs = context.Pipe()
            context.Process(target=self._serve, args=(theirs,), daemon=True).start()
            self.pipes.append(ours)

    @classmethod
    def _serve(cls, pipe):
        states = {}
        while True:
            command, *rest = pipe.recv()
            if command == "open":
                key, port, token, query = rest
                states[key] = {}
                start(stream, port, token, query, states[key])
            elif command == "ready":
                pipe.send({key: {name: state.get(name) for name in ("ready", "ready_seq", "error")}
                           for key, state in states.items()})
            else:
                for state in states.values():
                    hang_up(state)
                pipe.send({key: {**{name: state.get(name) for name in cls.FIELDS}, "delivered": len(state.get("ids", ()))}
                           for key, state in states.items()})
                return

    def open(self, port, token, query):
        key, self.count = self.count, self.count + 1
        self.pipes[key % len(self.pipes)].send(("open", key, port, token, query))
        return key

    def _ask(self, command):
        for pipe in self.pipes:
            pipe.send((command,))
        return {key: state for pipe in self.pipes for key, state in pipe.recv().items()}

    def ready(self):
        return self._ask("ready")

    def close(self):
        return self._ask("close")


def scenario(args, port, db):
    def connect():
        return http.client.HTTPConnection("127.0.0.1", port, timeout=60)

    distinct = [distinct_viewer(i) for i in range(args.identities)]
    private = None
    if args.path == "threads":
        extra = [actor for _, actor, group in distinct if group in ("room", "everything")]
        room = json.loads(call(connect(), "POST", "/api/v2/threads", "ana-test",
                               {"title": "Benchmark room", "audience": "members",
                                "members": ["human:ben", "human:cara", *extra]})[1])["id"]
        if distinct:
            private = json.loads(call(connect(), "POST", "/api/v2/threads", "ana-test", {
                "title": "Private room", "audience": "members",
                "members": [actor for _, actor, group in distinct if group == "private"]})[1])["id"]
        big = json.loads(call(connect(), "POST", "/api/v2/threads", "ana-test",
                              {"title": "Big room", "audience": "members",
                               "members": ["human:ben", "human:cara"]})[1])["id"]
        seed_followers(db, big, args.followers)
        scope, post_path, people = f"?thread={room}", f"/api/v2/threads/{room}/messages", TOKENS
        big_path = f"/api/v2/threads/{big}/messages"
    else:
        opened = json.loads(call(connect(), "POST", "/api/v2/messages", "ana-test",
                                 {"to": "ben", "text": "Benchmark room opened"})[1])
        room = opened["conversation_id"]
        scope, post_path, people = f"?conversation={room}", f"/api/v2/conversations/{room}/messages", DM_TOKENS
        big_path = None

    def opening(i):
        """Viewer `i`'s token and query."""
        if not distinct:
            return people[i % len(people)], scope
        token, _, group = distinct[i % len(distinct)]
        if group == "outsider":                        # each outsider asks for one thread it is not in
            return token, f"?thread={room if i % 10 == 3 else private}"
        return token, {"room": scope, "private": f"?thread={private}", "everything": "?"}[group]

    readers = Readers()
    viewer_keys = [readers.open(port, *opening(i)) for i in range(args.viewers)]
    deadline = time.time() + 60
    opened = {}
    while time.time() < deadline:
        opened = readers.ready()
        if len(opened) == args.viewers and all(v["ready"] is not None for v in opened.values()):
            break
        time.sleep(0.05)
    subscribed = sum(1 for v in opened.values() if v["ready"] is not None)
    if subscribed < args.viewers:
        raise SystemExit(f"only {subscribed} of {args.viewers} viewers were ready within 60 seconds")
    resume = next(v["ready_seq"] for v in opened.values() if v["ready_seq"] is not None)

    stop, slow = threading.Event(), {}
    start(slow_consumer, port, people[0], scope, slow, stop)
    time.sleep(0.5)

    room_writers = [{} for _ in range(args.writers)]
    big_writer = {}
    threads = [start(writer, port, people[i % len(people)], post_path, f"{room[:8]}-{i}", args.seconds, args.rate,
                     state, 0.0 if args.same_instant else i / len(room_writers)) for i, state in enumerate(room_writers)]
    if big_path:
        threads.append(start(writer, port, "ana-test", big_path, "big", args.seconds, args.rate, big_writer))
    private_writer = {}
    if private:
        threads.append(start(writer, port, "ana-test", f"/api/v2/threads/{private}/messages", PRIVATE_TAG.rstrip("-"),
                             args.seconds, args.rate, private_writer))

    time.sleep(args.seconds / 2)                       # a reconnect burst, halfway through the run
    def reopening(i):
        token, query = opening(i)
        return token, f"{query}{'&' if len(query) > 1 else ''}after={resume}"

    burst_keys = [readers.open(port, *reopening(i)) for i in range(args.burst)]
    for thread in threads:
        thread.join()
    stop.set()
    time.sleep(3)
    deadline = time.time() + 60
    while time.time() < deadline:
        opened = readers.ready()
        if all(opened[key]["ready"] is not None or opened[key]["error"] is not None for key in burst_keys):
            break
        time.sleep(0.05)
    final = readers.close()
    viewers, burst = [final[key] for key in viewer_keys], [final[key] for key in burst_keys]

    posted_ok = sum(w.get("ok", 0) for w in room_writers)
    statuses = {}
    for w in room_writers:
        for code, count in w.get("statuses", {}).items():
            statuses[str(code)] = statuses.get(str(code), 0) + count
    write_ms = [x for w in room_writers for x in w.get("writes", [])]
    latencies = [x for v in viewers for x in v.get("latencies", [])]
    groups = {}
    for i, v in enumerate(viewers):
        groups.setdefault(distinct[i % len(distinct)][2] if distinct else "room", []).append(v)
    per_viewer = [v["delivered"] for name in ("room", "everything") for v in groups.get(name, [])]
    ready = [b["ready"] for b in burst if b.get("ready") is not None]
    deadline = time.time() + SLOW_READ_SECONDS
    while time.time() < deadline and not slow.get("closed") and not slow.get("error"):
        time.sleep(0.05)
    hang_up(slow)
    return {
        "path": args.path,
        "room_writes": {"posted_ok": posted_ok, "achieved_per_second": round(posted_ok / args.seconds, 2),
                        "statuses": statuses, "ms": summary(write_ms),
                        "p95_target_ms": WRITE_P95_MS, "p95_met": (percentile(write_ms, .95) or 1e9) < WRITE_P95_MS},
        "big_thread_writes": None if not big_path else {
            "followers": args.followers, "statuses": big_writer.get("statuses", {}),
            "ms": summary(big_writer.get("writes", []))},
        "distinct_viewers": None if not distinct else {
            "identities": len(distinct), "people": sum(1 for _, actor, _ in distinct if actor.startswith("human:")),
            "bots": sum(1 for _, actor, _ in distinct if actor.startswith("bot:")),
            "private_posted": private_writer.get("ok", 0),
            "private_members_delivered_min": min((v["delivered"] for v in groups.get("private", [])), default=0),
            # Nothing of a thread reaches anyone outside it: these must be 0.
            "outsiders_delivered": sum(v["delivered"] for v in groups.get("outsider", [])),
            "private_seen_by_non_members": sum(v["private_seen"] for name in ("room", "everything", "outsider")
                                               for v in groups.get(name, [])),
            "burst_non_members_private_seen": sum(burst[i]["private_seen"] for i in range(len(burst))
                                                  if distinct[i % len(distinct)][2] != "private")},
        "viewers": {"count": args.viewers, "errors": [v["error"] for v in viewers if v.get("error")],
                    "delivered_min": min(per_viewer) if per_viewer else 0,
                    "delivered_max": max(per_viewer) if per_viewer else 0, "expected_each": posted_ok,
                    "event_seconds": summary(latencies), "event_p95_target_s": EVENT_P95_S,
                    "event_p95_met": (percentile(latencies, .95) or 1e9) < EVENT_P95_S},
        "reconnect_burst": {"clients": args.burst, "ready_seconds": summary(ready), "p95_target_s": BURST_P95_S,
                            "p95_met": (percentile(ready, .95) or 1e9) < BURST_P95_S,
                            "failed": sum(1 for b in burst if b.get("error") or b.get("status") != 200),
                            "resets": sum(1 for b in burst if b.get("reset"))},
        "slow_consumer": {k: slow.get(k) for k in ("status", "events_after_wait", "last_event", "reset", "closed", "error")},
        "resume_cursor": resume,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--preset", choices=sorted(PRESETS), default="default")
    parser.add_argument("--path", choices=("threads", "conversation"), default="threads")
    parser.add_argument("--seconds", type=int, default=60)
    parser.add_argument("--viewers", type=int, default=50)
    parser.add_argument("--writers", type=int, default=20)
    parser.add_argument("--rate", type=float, default=5.0)
    parser.add_argument("--followers", type=int, default=500)
    parser.add_argument("--burst", type=int, default=200)
    parser.add_argument("--identities", type=int, default=0, help="this many distinct viewers, people and bots (threads only)")
    parser.add_argument("--same-instant", action="store_true", help="every writer posts at the start of its period")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--serve", action="store_true", help=argparse.SUPPRESS)
    preset = parser.parse_known_args()[0].preset
    parser.set_defaults(**PRESETS[preset])          # explicit flags still win over the preset
    args = parser.parse_args()
    if args.serve:
        return serve()
    if min(args.seconds, args.viewers, args.writers, args.followers, args.burst) < 1 or args.rate <= 0:
        parser.error("counts and seconds must be positive")
    command = [sys.executable, str(Path(__file__).resolve()), "--serve"]
    if os.environ.get("BENCH_SAMPLE"):             # Python 3.15+: which code holds the interpreter lock, sampled
        command[1:1] = ["-m", "profiling.sampling", "run", "-a", "--mode", "gil", "-r", "500", "--collapsed",
                        "-o", os.environ["BENCH_SAMPLE"]]
    child = subprocess.Popen(command, cwd=str(ROOT), env={**os.environ, "BENCH_IDENTITIES": str(args.identities)},
                             stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
    line = child.stdout.readline()
    while line and not line.startswith("{"):
        line = child.stdout.readline()
    ready = json.loads(line or "{}")
    if "port" not in ready:
        child.kill()
        raise SystemExit("the server process did not start")
    try:
        result = scenario(args, ready["port"], ready["db"])
        result["db_rows"] = db_counts(ready["db"])
    finally:
        child.stdin.close()
        tail = child.stdout.read()
        child.wait(timeout=300)
    lines = [line for line in tail.splitlines() if line.startswith("{")]   # the sampler prints its own lines too
    result["server_profile"] = json.loads(lines[-1]).get("profile") if lines else None
    result.update({"platform": platform.platform(), "python": sys.version.split()[0], "cpu_count": os.cpu_count(),
                   "load_average": os.getloadavg(), "parameters": {k: str(v) for k, v in vars(args).items()},
                   "targets": {"write_p95_ms": WRITE_P95_MS, "event_p95_s": EVENT_P95_S,
                               "burst_ready_p95_s": BURST_P95_S}})
    text = json.dumps(result, indent=2)
    if args.output:
        args.output.write_text(text + "\n", encoding="utf-8")
    print(text)
    if not result["viewers"]["event_seconds"]["n"]:
        raise SystemExit("no live event reached a viewer: the measurement is void")


if __name__ == "__main__":
    main()
