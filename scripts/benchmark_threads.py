#!/usr/bin/env python3
"""Thread write and live-event timings (docs/dev/threads-contract.md §9) on owned loopback services, fictional data.

Targets: a write's p95 under 150 ms, and an event's p95 under 1 s from the writer's send to the viewer's read. One run
holds, for --seconds: --viewers live streams on one thread (GET /api/v2/events?thread=), --writers humans posting to it
at --rate messages a second each (POST /api/v2/threads/{id}/messages), one writer on a thread with --followers
followers, a --burst of clients reconnecting together halfway through, and one slow consumer that never reads.
Writer and viewers share one process clock, so latencies are exact to the scheduler, not a network estimate.
Run it on a clean VM with scripts/remote.py, not on a laptop, and keep the output with the commit it measured.
"""

import argparse
import http.client
import json
import os
import platform
import re
import socket
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

from backend import events as E  # noqa: E402
from backend import hubdb as H  # noqa: E402
from backend.tests import test_api  # noqa: E402

WRITE_P95_MS, EVENT_P95_S = 150, 1.0
SENT = re.compile(r"sent=([0-9.]+)")
TOKENS = ["ana-test", "ben-test", "cara-test"]          # the fixture's people; members of both threads


def percentile(values, share):
    values = sorted(values)
    return values[int(share * (len(values) - 1))] if values else None


def summary(values):
    return {"n": len(values), "p50": percentile(values, .5), "p95": percentile(values, .95),
            "max": max(values) if values else None}


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


def stream(port, token, query, state, started=None):
    """Read one live stream to its end: the ready time, the latency of each of this run's messages, and resets."""
    state.update({"ready": None, "latencies": [], "ids": set(), "reset": False, "error": None, "status": None})
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
            elif name == "messages":
                data = json.loads(event.get("data") or "{}")
                body = (data.get("message") or {}).get("body") or ""     # a messages event nests the message
                match = SENT.search(str(body))
                if match and data.get("id") not in state["ids"]:
                    state["ids"].add(data["id"])
                    state["latencies"].append(time.time() - float(match.group(1)))
            elif name == "reset":
                state["reset"] = True
            event = {}
    except (OSError, ValueError, http.client.HTTPException) as error:
        state["error"] = repr(error)


def slow_consumer(port, token, query, state, stop):
    """Opens a stream with a small receive buffer and reads nothing until the run ends, then reads what came."""
    state.update({"status": None, "events_after_wait": 0, "reset": False, "closed": False, "error": None})
    try:
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=300)
        state["conn"] = conn
        conn.connect()
        conn.sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 4096)
        conn.request("GET", "/api/v2/events" + query, headers={"Authorization": f"Bearer {token}"})
        response = conn.getresponse()
        state["status"] = response.status
        stop.wait()
        for raw in response:
            line = raw.decode().rstrip("\n")
            if line.startswith("event: "):
                state["events_after_wait"] += 1
                state["reset"] = state["reset"] or line == "event: reset"
        state["closed"] = True
    except (OSError, ValueError, http.client.HTTPException) as error:
        state["error"] = repr(error)


def writer(port, token, thread_id, seconds, rate, state):
    """Posts at `rate` a second for `seconds`; each message carries its send time for the viewers to measure."""
    state.update({"writes": [], "ok": 0, "statuses": {}, "error": None})
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=60)
    period, end, next_at, n = 1 / rate, time.time() + seconds, time.time(), 0
    while time.time() < end:
        time.sleep(max(0, next_at - time.time()))
        next_at += period
        n += 1
        text = f"Benchmark message {thread_id[:8]}-{n} sent={time.time():.6f}"
        began = time.perf_counter()
        status, _ = call(conn, "POST", f"/api/v2/threads/{thread_id}/messages", token, {"text": text})
        state["writes"].append((time.perf_counter() - began) * 1000)
        state["statuses"][status] = state["statuses"].get(status, 0) + 1
        state["ok"] += status == 200


def seed_followers(app, thread_id, count):
    """Followers for the big thread: thread_members and thread_followers rows, as a company's roster would make."""
    now = H.now()
    with app.state.store.transaction() as c:
        c.executemany("INSERT INTO thread_members(id,thread_id,actor,role,added_by,created_at,updated_at) "
                      "VALUES(?,?,?,?,?,?,?)", [(uuid.uuid4().hex, thread_id, f"human:member-{i:04d}", "member",
                                                 "human:ana", now, now) for i in range(count)])
        c.executemany("INSERT INTO thread_followers(id,thread_id,actor,following,notify,reason,last_read_seq,"
                      "created_at,updated_at) VALUES(?,?,?,1,'live','manual',0,?,?)",
                      [(uuid.uuid4().hex, thread_id, f"human:member-{i:04d}", now, now) for i in range(count)])


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


def run(args, port, app):
    room = json.loads(call(http.client.HTTPConnection("127.0.0.1", port, timeout=60), "POST", "/api/v2/threads",
                           "ana-test", {"title": "Benchmark room", "audience": "members",
                                        "members": ["human:ben", "human:cara"]})[1])["id"]
    big = json.loads(call(http.client.HTTPConnection("127.0.0.1", port, timeout=60), "POST", "/api/v2/threads",
                          "ana-test", {"title": "Big room", "audience": "members",
                                       "members": ["human:ben", "human:cara"]})[1])["id"]
    seed_followers(app, big, args.followers)

    viewers = [{} for _ in range(args.viewers)]
    for i, state in enumerate(viewers):
        start(stream, port, TOKENS[i % len(TOKENS)], f"?thread={room}", state)
    deadline = time.time() + 60
    while time.time() < deadline and any(v.get("ready") is None for v in viewers):
        time.sleep(0.05)
    for state in viewers:
        state.setdefault("ready", None)
    subscribed = sum(1 for v in viewers if v.get("ready") is not None)
    if subscribed < args.viewers:
        raise SystemExit(f"only {subscribed} of {args.viewers} viewers were ready within 60 seconds")

    stop, slow = threading.Event(), {}
    start(slow_consumer, port, TOKENS[0], f"?thread={room}", slow, stop)
    time.sleep(0.5)

    room_writers = [{} for _ in range(args.writers)]
    big_writer = {}
    threads = [start(writer, port, TOKENS[i % len(TOKENS)], room, args.seconds, args.rate, state)
               for i, state in enumerate(room_writers)]
    threads.append(start(writer, port, "ana-test", big, args.seconds, args.rate, big_writer))

    time.sleep(args.seconds / 2)                       # a reconnect burst, halfway through the run
    with app.state.store.read() as c:
        resume = E.latest(c)
    burst = [{} for _ in range(args.burst)]
    for i, state in enumerate(burst):
        start(stream, port, TOKENS[i % len(TOKENS)], f"?thread={room}&after={resume}", state)
    for thread in threads:
        thread.join()
    stop.set()
    time.sleep(2)
    time.sleep(1)
    deadline = time.time() + 60
    while time.time() < deadline and any(b.get("ready") is None and b.get("error") is None for b in burst):
        time.sleep(0.05)

    posted_ok = sum(w.get("ok", 0) for w in room_writers)
    statuses = {}
    for w in room_writers:
        for code, count in w.get("statuses", {}).items():
            statuses[str(code)] = statuses.get(str(code), 0) + count
    write_ms = [x for w in room_writers for x in w.get("writes", [])]
    latencies = [x for v in viewers for x in v.get("latencies", [])]
    per_viewer = [len(v.get("ids", ())) for v in viewers]
    ready = [b["ready"] for b in burst if b.get("ready") is not None]
    for state in viewers + burst + [slow]:
        hang_up(state)
    return {
        "room_writes": {"posted_ok": posted_ok, "statuses": statuses, "ms": summary(write_ms),
                        "p95_target_ms": WRITE_P95_MS, "p95_met": (percentile(write_ms, .95) or 1e9) < WRITE_P95_MS},
        "big_thread_writes": {"followers": args.followers, "statuses": big_writer.get("statuses", {}),
                              "ms": summary(big_writer.get("writes", []))},
        "viewers": {"count": args.viewers, "errors": [v["error"] for v in viewers if v.get("error")],
                    "delivered_min": min(per_viewer) if per_viewer else 0,
                    "delivered_max": max(per_viewer) if per_viewer else 0, "expected_each": posted_ok,
                    "event_seconds": summary(latencies), "event_p95_target_s": EVENT_P95_S,
                    "event_p95_met": (percentile(latencies, .95) or 1e9) < EVENT_P95_S},
        "reconnect_burst": {"clients": args.burst, "ready_seconds": summary(ready),
                            "failed": sum(1 for b in burst if b.get("error") or b.get("status") != 200),
                            "resets": sum(1 for b in burst if b.get("reset"))},
        "slow_consumer": {k: slow.get(k) for k in ("status", "events_after_wait", "reset", "closed", "error")},
        "resume_cursor": resume,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--seconds", type=int, default=60)
    parser.add_argument("--viewers", type=int, default=50)
    parser.add_argument("--writers", type=int, default=20)
    parser.add_argument("--rate", type=float, default=5.0)
    parser.add_argument("--followers", type=int, default=500)
    parser.add_argument("--burst", type=int, default=200)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if min(args.seconds, args.viewers, args.writers, args.followers, args.burst) < 1 or args.rate <= 0:
        parser.error("counts and seconds must be positive")
    with tempfile.TemporaryDirectory(prefix="threads-benchmark-") as directory:
        generator, server, thread, port, app = boot(directory)
        try:
            result = run(args, port, app)
        finally:
            server.should_exit = True
            thread.join(10)
            generator.close()
    result.update({"platform": platform.platform(), "python": sys.version.split()[0], "cpu_count": os.cpu_count(),
                   "load_average": os.getloadavg(), "parameters": {k: str(v) for k, v in vars(args).items()},
                   "targets": {"write_p95_ms": WRITE_P95_MS, "event_p95_s": EVENT_P95_S}})
    text = json.dumps(result, indent=2)
    if args.output:
        args.output.write_text(text + "\n", encoding="utf-8")
    print(text)
    if not result["viewers"]["event_seconds"]["n"]:
        raise SystemExit("no live event reached a viewer: the measurement is void")


if __name__ == "__main__":
    main()
