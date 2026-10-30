"""setup/cell_wake.py: a sleeping company's first requests are held while its server starts once, then sent on to it
as they came, and answered by it. Run on the cell's fake System, against a fake server on loopback."""
import http.client
import json
import socket
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from setup import cell, cell_wake as W
from setup.tests.test_cell import FakeSystem, ROOT, add_args, host, ready, record, utc_in  # noqa: F401


class Upstream(BaseHTTPRequestHandler):
    """The company's server: answers 201 with what it got, in two chunks."""
    protocol_version = "HTTP/1.1"

    def do_any(self):
        if self.headers.get("Transfer-Encoding", "").lower() == "chunked":
            body = b""
            while (size := int(self.rfile.readline().strip(), 16)):
                body += self.rfile.read(size)
                self.rfile.readline()
            self.rfile.readline()
        else:
            body = self.rfile.read(int(self.headers.get("Content-Length") or 0))
        self.server.seen.append({"method": self.command, "path": self.path, "headers": dict(self.headers),
                                 "body": body})
        out = json.dumps({"method": self.command, "path": self.path, "length": len(body),
                          "body": body.decode(errors="replace")[:100]}).encode()
        self.send_response(201, "Made")
        self.send_header("Content-Type", "application/json")
        self.send_header("Set-Cookie", "a=1")
        self.send_header("Transfer-Encoding", "chunked")
        self.end_headers()
        for part in (out[:5], out[5:]):
            self.wfile.write(b"%x\r\n%s\r\n" % (len(part), part))
            self.wfile.flush()
        self.wfile.write(b"0\r\n\r\n")

    do_GET = do_POST = do_PUT = do_HEAD = do_any

    def log_message(self, *a):
        pass


@pytest.fixture
def upstream():
    server = ThreadingHTTPServer(("127.0.0.1", 0), Upstream)
    server.seen = []
    threading.Thread(target=server.serve_forever, args=(0.05,), daemon=True).start()
    yield server
    server.shutdown()
    server.server_close()


class Slow:
    """Makes `docker compose up` take `seconds` of real time, and counts the starts."""

    def __init__(self, system, seconds=0.0):
        self.system, self.seconds, self.ups = system, seconds, 0
        self.real = system.run
        system.run = self.run

    def run(self, argv, **kw):
        if "--project-name" in argv and "up" in argv and argv[argv.index("--project-name") + 1] != cell.EDGE_PROJECT:
            self.ups += 1
            time.sleep(self.seconds)
        return self.real(argv, **kw)


@pytest.fixture
def asleep(ready, upstream):
    """boatyard added and put to sleep, and a wake service for it on a free loopback port."""
    ready(*add_args(ready))
    ready("sleep", "--company", "boatyard")
    made = {}

    def start(max_connections=W.MAX_CONNECTIONS, **kw):
        svc = W.Service(ready.root, ready.system, upstream=lambda c: upstream.server_address,
                        make_cell=lambda: cell.Cell(ready.root, ready.system, ready.etc, ROOT / "compose.yaml"), **kw)
        made["listener"] = W.serve(svc, ("127.0.0.1", 0), max_connections)
        svc.port = made["listener"].server_address[1]
        return svc

    ready.start = start
    yield ready
    if "listener" in made:
        made["listener"].shutdown()
        made["listener"].server_close()


def send(svc, method="GET", path="/", body=None, headers=None, slug="boatyard", host="boatyard.tico.team",
         timeout=20):
    c = http.client.HTTPConnection("127.0.0.1", svc.port, timeout=timeout)
    h = {"Host": host, **({cell.WAKE_HEADER: slug} if slug is not None else {}), **(headers or {})}
    c.request(method, path, body=body, headers=h, encode_chunked=bool(h.get("Transfer-Encoding")))
    r = c.getresponse()
    data = r.read()
    c.close()
    return r, data


def test_concurrent_requests_share_one_start_and_get_the_servers_answers(asleep, upstream):
    slow = Slow(asleep.system, 0.4)
    svc = asleep.start()
    answers = [None] * 12

    def one(i):
        answers[i] = send(svc, "POST", f"/api/v2/things?i={i}", body=f"body {i}".encode(),
                          headers={"Content-Type": "text/plain", "X-Trace": str(i)})
    threads = [threading.Thread(target=one, args=(i,)) for i in range(12)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(30)
    assert slow.ups == 1 and svc.starts == 1
    for i, (r, data) in enumerate(answers):
        assert (r.status, r.reason) == (201, "Made")
        assert json.loads(data) == {"method": "POST", "path": f"/api/v2/things?i={i}", "length": len(f"body {i}"),
                                    "body": f"body {i}"}
        assert r.getheader("Set-Cookie") == "a=1" and r.getheader("Content-Type") == "application/json"
    got = {s["headers"]["X-Trace"]: s for s in upstream.seen}
    assert len(got) == 12 and got["3"]["headers"]["Host"] == "boatyard.tico.team"
    assert cell.WAKE_HEADER not in got["3"]["headers"]       # the route's own header goes no further
    assert record(asleep)["state"] == "running" and "reverse_proxy 127.0.0.1:20000" in \
        (asleep.root / "router" / "Caddyfile").read_text()
    # awake: the next request is proxied straight away, with no start
    r, _ = send(svc, "PUT", "/x", body=b"y")
    assert r.status == 201 and slow.ups == 1


def test_a_chunked_body_is_streamed_through(asleep, upstream):
    svc = asleep.start()
    parts = [b"a" * 70000, b"b" * 10, b"c" * 3]
    r, data = send(svc, "POST", "/upload", body=iter(parts), headers={"Transfer-Encoding": "chunked"})
    assert r.status == 201 and json.loads(data)["length"] == 70013
    assert upstream.seen[-1]["body"] == b"".join(parts)


def test_a_failed_wake_answers_503_and_does_not_start_again_at_once(asleep, upstream):
    asleep.system.health["boatyard.tico.team"] = 500
    slow = Slow(asleep.system)
    svc = asleep.start(start=1)
    r, data = send(svc, "POST", "/x", body=b"z")
    assert r.status == 503 and r.getheader("Retry-After") == "5" and data == (cell.HOLD_BODY + "\n").encode()
    assert r.getheader("Content-Type").startswith("text/plain") and b"<" not in data
    assert record(asleep)["state"] == "sleeping" and asleep.system.ps["boatyard"][0] == "exited"
    r, _ = send(svc, "GET", "/")
    assert r.status == 503 and slow.ups == 1 and svc.starts == 1      # within FAILED_FOR: no start loop
    assert upstream.seen == []


def test_a_wake_slower_than_the_hold_answers_503_and_finishes(asleep):
    slow = Slow(asleep.system, 0.8)
    svc = asleep.start(hold=0.2)
    began = time.monotonic()
    r, _ = send(svc, "GET", "/")
    assert r.status == 503 and r.getheader("Retry-After") == "5" and time.monotonic() - began < 0.7
    deadline = time.monotonic() + 10
    while record(asleep)["state"] != "running":
        assert time.monotonic() < deadline
        time.sleep(0.05)
    assert slow.ups == 1


def test_health_probes_never_wake_a_company(asleep):
    slow = Slow(asleep.system)
    svc = asleep.start()
    for method in ("GET", "HEAD"):
        r, _ = send(svc, method, "/healthz")
        assert r.status == 503 and r.getheader("Retry-After") == "5"
    assert slow.ups == 0 and svc.starts == 0 and record(asleep)["state"] == "sleeping"


@pytest.mark.parametrize("slug, name", [(None, "boatyard.tico.team"), ("other", "boatyard.tico.team"),
                                        ("boatyard", "other.tico.team"), ("../boatyard", "boatyard.tico.team"),
                                        ("boatyard", "")])
def test_only_the_company_the_route_names_on_its_own_host(asleep, slug, name):
    slow = Slow(asleep.system)
    svc = asleep.start()
    r, data = send(svc, slug=slug, host=name)
    assert r.status == 404 and slow.ups == 0 and record(asleep)["state"] == "sleeping"


def test_an_alias_host_is_its_own_too(asleep, upstream):
    c = record(asleep)
    c["aliases"] = ["boats.example.com"]
    (asleep.root / "companies" / "boatyard" / "company.json").write_text(json.dumps(c))
    svc = asleep.start()
    r, _ = send(svc, host="boats.example.com:443")
    assert r.status == 201


def test_a_stopped_company_is_not_woken(asleep):
    asleep("start", "--company", "boatyard")
    asleep("stop", "--company", "boatyard")
    slow = Slow(asleep.system)
    svc = asleep.start()
    r, data = send(svc)
    assert r.status == 503 and slow.ups == 0 and b"stopped" in data


def test_a_body_over_the_servers_limit_is_refused_before_a_start(asleep, upstream, monkeypatch):
    monkeypatch.setattr(W, "UPLOAD_MARGIN", 0)
    env = asleep.root / "companies" / "boatyard" / ".env"
    env.write_text(env.read_text() + "TICO_UPLOAD_MAX_BYTES=10\n")
    slow = Slow(asleep.system)
    svc = asleep.start()
    r, _ = send(svc, "POST", "/x", body=b"x" * 11)
    assert r.status == 413 and slow.ups == 0
    r, _ = send(svc, "POST", "/x", body=iter([b"x" * 6, b"x" * 6]), headers={"Transfer-Encoding": "chunked"})
    assert r.status == 413 and upstream.seen == []         # streamed: refused once past the limit, never sent whole
    r, _ = send(svc, "POST", "/x", body=b"x" * 10)
    assert r.status == 201


def test_held_requests_are_capped_per_company(asleep):
    Slow(asleep.system, 0.6)
    svc = asleep.start(max_held_per_company=1)
    first = threading.Thread(target=send, args=(svc,))
    first.start()
    deadline = time.monotonic() + 5
    while not svc.held:
        assert time.monotonic() < deadline
        time.sleep(0.01)
    began = time.monotonic()
    r, data = send(svc)
    assert r.status == 503 and r.getheader("Retry-After") == "5" and time.monotonic() - began < 0.4
    first.join(10)
    assert svc.held == {}


def test_connections_are_capped(asleep):
    svc = asleep.start(max_connections=1)
    idle = socket.create_connection(("127.0.0.1", svc.port))      # holds the only place, sending nothing
    try:
        time.sleep(0.1)
        r, data = send(svc, timeout=5)
        assert r.status == 503 and r.getheader("Retry-After") == "5"
    finally:
        idle.close()
    deadline = time.monotonic() + 5
    while True:                                                   # the place is free again
        r, _ = send(svc)
        if r.status == 201:
            break
        assert time.monotonic() < deadline
        time.sleep(0.05)


def test_a_websocket_wakes_the_company_then_is_asked_to_connect_again(asleep, upstream):
    slow = Slow(asleep.system)
    svc = asleep.start()
    r, _ = send(svc, headers={"Connection": "Upgrade", "Upgrade": "websocket"})
    assert r.status == 503 and r.getheader("Retry-After") == "1"
    assert slow.ups == 1 and record(asleep)["state"] == "running" and upstream.seen == []


def test_a_server_that_does_not_answer_after_the_wake_is_a_503_not_a_502(asleep):
    svc = asleep.start()
    svc.upstream = lambda c: ("127.0.0.1", 1)                      # nothing listens
    r, data = send(svc)
    assert r.status == 503 and data == (cell.HOLD_BODY + "\n").encode()


def test_the_wake_unit_runs_this_module():
    unit = cell.wake_unit(ROOT, "/usr/bin/python3", "/srv/tico-cell")
    assert "ExecStart=/usr/bin/python3 -m setup.cell_wake --root /srv/tico-cell" in unit
    assert "Restart=always" in unit and "RestartSec=1" in unit
    assert (W.__name__, cell.WAKE_PORT) == ("setup.cell_wake", 8082)


# --- timed wakes: the record's wake_at starts the company without a request (WAKE_SWEEP loop) ----------------------

def settled(svc, slug="boatyard"):
    """The start the last sweep began (or joined) has finished, one way or the other."""
    a = svc.attempts.get(slug)
    assert a is not None and a.done.wait(30)
    return a


def test_a_timed_wake_due_within_a_minute_starts_the_company(asleep):
    slow = Slow(asleep.system)
    svc = asleep.start()
    asleep("sleep", "--company", "boatyard", "--wake-at", utc_in(30))     # already asleep: only the time changes
    assert svc.sweep() == ["boatyard"]
    assert settled(svc).ok
    assert record(asleep)["state"] == "running" and "wake_at" not in record(asleep)
    assert slow.ups == 1 and svc.starts == 1
    assert "reverse_proxy 127.0.0.1:20000" in (asleep.root / "router" / "Caddyfile").read_text()


def test_a_timed_wake_not_yet_due_leaves_the_company_asleep(asleep):
    slow = Slow(asleep.system)
    svc = asleep.start()
    later = utc_in(3600)
    asleep("sleep", "--company", "boatyard", "--wake-at", later)
    assert svc.sweep() == [] and svc.starts == 0 and slow.ups == 0
    assert record(asleep)["state"] == "sleeping" and record(asleep)["wake_at"] == later


def test_a_stored_wake_at_that_is_not_a_time_wakes_nothing(asleep):
    slow = Slow(asleep.system)
    svc = asleep.start()
    c = record(asleep)
    c["wake_at"] = "soon"
    (asleep.root / "companies" / "boatyard" / "company.json").write_text(json.dumps(c))
    assert svc.sweep() == [] and svc.starts == 0 and slow.ups == 0 and record(asleep)["state"] == "sleeping"


def test_a_failed_timed_wake_is_tried_again_on_a_later_sweep_not_at_once(asleep):
    asleep.system.health["boatyard.tico.team"] = 500
    slow = Slow(asleep.system)
    svc = asleep.start()
    asleep("sleep", "--company", "boatyard", "--wake-at", utc_in(10))
    assert svc.sweep() == ["boatyard"] and not settled(svc).ok
    assert record(asleep)["state"] == "sleeping" and record(asleep)["wake_at"]      # the time stays until it works
    assert svc.sweep() == ["boatyard"] and svc.starts == 1                     # within FAILED_FOR: no start loop
    asleep.system.health.pop("boatyard.tico.team")
    svc.failed_for = 0                                                         # as if FAILED_FOR had passed
    assert svc.sweep() == ["boatyard"] and settled(svc).ok
    assert slow.ups == 2 and svc.starts == 2 and record(asleep)["state"] == "running"
    assert "wake_at" not in record(asleep)


def test_the_timed_wake_loop_sweeps_at_once_and_returns_when_stopped(asleep):
    svc = asleep.start()
    asleep("sleep", "--company", "boatyard", "--wake-at", utc_in(5))
    stopping = threading.Event()
    stopping.set()
    svc.timed_wakes(stopping)                                                  # one sweep, then it returns
    assert settled(svc).ok and record(asleep)["state"] == "running"
