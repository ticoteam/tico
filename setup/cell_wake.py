"""Wakes a sleeping cell company on its first request, and answers that request (docs/cell.md, "Sleep, wake and the hold").

`cell sleep` stops a company's server and sends its hosts to this service (127.0.0.1:8082, setup/cell.py WAKE_PORT)
instead of to the server. A request that comes here is held while the company's server starts (setup/cell.py
`wake_company`: the cell's own start, under the company's lock, healthy on its own port, then marked running and the
router reloaded so later requests go straight to the server), then sent on to the server as it came (method, headers,
body, streamed both ways), and the server's answer is the answer. Many requests during one wake share one start. A
wake that fails, or does not finish within WAKE_HOLD, is answered 503 with Retry-After and a short plain body; so is a
request over a cap. Nothing here answers 502.

Timed wakes: a sleeping company's record can name the time it should wake (`sleep --wake-at`, or the updater's POST
/sleep). Every WAKE_SWEEP seconds, `timed_wakes` starts each sleeping company whose wake_at is at or before now plus
WAKE_AHEAD, through the same `attempt` a request uses, so a request that comes meanwhile shares that start. A start that
fails is logged and tried again on a later sweep (not at once: `attempt` keeps a failure for FAILED_FOR). The loop holds
no lock while it waits, and a sweep only reads the records.

Which company: the router's route for a sleeping company sets WAKE_HEADER to that company's slug (`header_up` replaces
any copy the visitor sent), and the request's Host must be one of that company's own hosts. Nothing in the request
picks another company, and the listener is on loopback, where only the router (and root on the host) can reach it.
Starting a company is also all a stranger can make it do, and any visitor to its domain can do that.

What it does not wake: GET or HEAD /healthz is answered 503 at once (cell stats and monitors probe every company; a
probe must not undo a sleep). A WebSocket upgrade wakes the company and is then answered 503 with Retry-After: 1, since
this proxy speaks plain HTTP only; the client's reconnect goes straight to the server.

Caps: at most MAX_CONNECTIONS connections at once (each is one thread), MAX_HELD requests waiting for a wake in all
and MAX_HELD_PER_COMPANY for one company; past them, 503 at once. A request body is streamed, never kept, and refused
(413) over what the server itself accepts (TICO_UPLOAD_MAX_BYTES, 2 GiB by default, plus a multipart margin; the
server applies its tighter limits per route itself).

One process (systemd unit tico-cell-wake, `cell init`), its own and not the cell updater's: the updater's stop waits
up to 30 minutes for an update under way and its listeners face the companies' own networks; this one faces the
router, must come back within a second when it stops, and a flood of visitors here must not hold up an update there.
It runs as root because the work is `wake` (Docker, the company's folder, the router's reload), as staff's commands do.
"""
from __future__ import annotations

import argparse
import contextlib
import datetime
import http.client
import os
import signal
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from . import cell as C, contract, envfile

WAKE_HOLD = 30.0                 # how long a request waits for its company to wake
WAKE_START = 300                 # how long the start itself may take (a pending update's migration); later requests join
WAKE_LOCK_WAIT = 300.0           # for the company's lock, held by another cell command (an update, `sleep`)
FAILED_FOR = 10.0                # a failed wake is answered 503 at once this long, so a flood is not a start loop
WAKE_SWEEP = 20.0                # how often the timed wakes are checked
WAKE_AHEAD = 60.0                # a timed wake starts this long before its time, so the server is up when it is due
MAX_CONNECTIONS = 512
MAX_HELD = 256
MAX_HELD_PER_COMPANY = 64
UPLOAD_DEFAULT = 2 * 1024 ** 3   # the server's own default (backend/config.py upload_max_bytes)
UPLOAD_MARGIN = 11_000_000       # the multipart margin it allows on top (backend/app.py)
PROXY_TIMEOUT = 330.0            # the longest quiet gap in an answer (an SSE stream sends a keepalive every 15 s)
CHUNK = 64 * 1024
HOP = frozenset({"connection", "keep-alive", "proxy-connection", "te", "trailer", "transfer-encoding", "upgrade",
                 "proxy-authorization", "proxy-authenticate", "expect", "content-length"})


def log(text: str) -> None:
    print("tico-cell-wake: " + text, file=sys.stderr, flush=True)


class Attempt:
    """One wake of one company: every request held for it waits on `done`."""

    def __init__(self):
        self.done = threading.Event()
        self.ok = False
        self.port = 0
        self.error = ""
        self.finished = 0.0


class Service:
    """The wakes under way, the held counts, and the work. `make_cell` gives a fresh setup/cell.py Cell per use (tests
    pass one on a fake System); `upstream` is where a company's server is reached (tests point it at a fake server)."""

    def __init__(self, root: Path, system: C.System | None = None, *, make_cell=None, upstream=None,
                 hold: float = WAKE_HOLD, start: int = WAKE_START, failed_for: float = FAILED_FOR,
                 max_held: int = MAX_HELD, max_held_per_company: int = MAX_HELD_PER_COMPANY):
        self.root, self.system = Path(root), system or C.System()
        self.make_cell = make_cell or (lambda: C.Cell(self.root, self.system))
        self.upstream = upstream or (lambda c: (C.ROUTER_ADDRESS, int(c["port"])))
        self.hold, self.start, self.failed_for = hold, start, failed_for
        self.max_held, self.max_held_per_company = max_held, max_held_per_company
        self.guard = threading.Lock()
        self.attempts: dict[str, Attempt] = {}
        self.held: dict[str, int] = {}
        self.workers: set[threading.Thread] = set()
        self.starts = 0                 # wakes begun, for the tests and the log

    def company(self, slug: str, host: str) -> dict | None:
        """The record the route named, only when it is not removed and the request's Host is one of its own."""
        if not C.SLUG_RE.fullmatch(slug or "") or "--" in slug:
            return None
        c = self.make_cell().load(slug)
        name = (host or "").strip().lower().rsplit(":", 1)[0] if not (host or "").startswith("[") else ""
        if not c or c.get("state") == "removed" or name not in C.hosts_of(c):
            return None
        return c

    def sweep(self) -> list[str]:
        """Starts every sleeping company whose timed wake (its record's wake_at) is at or before now + WAKE_AHEAD,
        through `attempt` (one start however many sweeps or requests ask). Returns the companies it started or
        joined."""
        now, started = time.time(), []
        for c in self.make_cell().companies():
            if c.get("state") != "sleeping" or not c.get("wake_at"):
                continue
            try:
                due = datetime.datetime.strptime(c["wake_at"], C.WAKE_AT_FORMAT).replace(
                    tzinfo=datetime.timezone.utc).timestamp()
            except (TypeError, ValueError):
                log(f"{c.get('slug')}: wake_at {c.get('wake_at')!r} is not a time; not woken on it")
                continue
            if due <= now + WAKE_AHEAD:
                self.attempt(c["slug"])
                started.append(c["slug"])
        return started

    def timed_wakes(self, stopping: threading.Event) -> None:
        """The timed-wake loop: a sweep, then WAKE_SWEEP seconds asleep, until `stopping` is set. A sweep that raises is
        logged and the loop goes on. The first sweep is at once, so a company due while the service was down wakes."""
        while True:
            try:
                self.sweep()
            except Exception as e:  # noqa: BLE001  logged; the next sweep tries again
                log(f"timed wakes: {type(e).__name__}: {e}")
            if stopping.wait(WAKE_SWEEP):
                return

    def body_limit(self, slug: str) -> int:
        values = envfile.parse(C._read(self.root / "companies" / slug / ".env"))
        try:
            limit = int(values.get("TICO_UPLOAD_MAX_BYTES") or UPLOAD_DEFAULT)
        except ValueError:
            limit = UPLOAD_DEFAULT
        return max(limit, 0) + UPLOAD_MARGIN

    @contextlib.contextmanager
    def held_slot(self, slug: str):
        """A place among the held requests, or None when the caps are reached."""
        with self.guard:
            total = sum(self.held.values())
            ok = total < self.max_held and self.held.get(slug, 0) < self.max_held_per_company
            if ok:
                self.held[slug] = self.held.get(slug, 0) + 1
        try:
            yield ok
        finally:
            if ok:
                with self.guard:
                    self.held[slug] -= 1
                    if not self.held[slug]:
                        del self.held[slug]

    def attempt(self, slug: str) -> Attempt:
        """The wake under way for `slug`, a failed one still within FAILED_FOR, or a new one started now."""
        now = time.monotonic()
        with self.guard:
            a = self.attempts.get(slug)
            if a and (not a.done.is_set() or (not a.ok and now - a.finished < self.failed_for)):
                return a
            a = self.attempts[slug] = Attempt()
            worker = threading.Thread(target=self.wake, args=(slug, a), name="wake-" + slug, daemon=True)
            self.workers.add(worker)
            self.starts += 1
        worker.start()
        return a

    def wake(self, slug: str, a: Attempt) -> None:
        cell = self.make_cell()
        cell.lock_wait = WAKE_LOCK_WAIT
        began = time.monotonic()
        try:
            c, ready, _ = cell.wake_company(slug, self.start)
            a.ok, a.port = True, int(c["port"])
            log(f"{slug}: awake in {ready} ms")
        except C.CellError as e:
            c = cell.load(slug) or {}
            if c.get("state") == "running":         # awake, though the router's reload or an update's release failed
                a.ok, a.port = True, int(c["port"])
            a.error = cell.scrub(e.detail)[:300]
            log(f"{slug}: wake: {e.code}: {a.error}")
        except Exception as e:  # noqa: BLE001  the held requests get their 503, rather than a dead thread saying nothing
            a.error = cell.scrub(f"{type(e).__name__}: {e}")[:300]
            log(f"{slug}: wake failed: {a.error}")
        finally:
            a.finished = time.monotonic()
            a.done.set()
            with self.guard:
                self.workers.discard(threading.current_thread())
            if not a.ok:
                log(f"{slug}: still asleep after {int((a.finished - began) * 1000)} ms")


class Listener(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True
    request_queue_size = 128

    def __init__(self, service: Service, address: tuple[str, int], max_connections: int = MAX_CONNECTIONS):
        self.service = service
        self.slots = threading.BoundedSemaphore(max_connections)
        super().__init__(address, Handler)

    def process_request(self, request, client_address):
        """One thread per connection, up to the cap; past it, a 503 written here and the connection closed."""
        if not self.slots.acquire(blocking=False):
            with contextlib.suppress(OSError):
                request.settimeout(1)
                request.sendall(plain(503, "Too many requests are waiting; try again in a few seconds.", 5))
            self.shutdown_request(request)
            return
        try:
            super().process_request(request, client_address)
        except BaseException:
            self.slots.release()
            raise

    def process_request_thread(self, request, client_address):
        try:
            super().process_request_thread(request, client_address)
        finally:
            self.slots.release()


def plain(code: int, text: str, retry: int | None = None) -> bytes:
    """A whole short plain-text answer, as bytes."""
    body = (text + "\n").encode()
    reason = http.client.responses.get(code, "")
    head = [f"HTTP/1.0 {code} {reason}", "Content-Type: text/plain; charset=utf-8", f"Content-Length: {len(body)}",
            "Cache-Control: no-store", "Connection: close"] + ([f"Retry-After: {retry}"] if retry is not None else [])
    return ("\r\n".join(head) + "\r\n\r\n").encode() + body


class TooLarge(Exception):
    pass


class Handler(BaseHTTPRequestHandler):
    server_version = "tico-cell-wake"
    timeout = 60                        # a client that sends nothing does not keep a thread

    def answer(self, code: int, text: str, retry: int | None = None) -> None:
        with contextlib.suppress(OSError):
            self.wfile.write(plain(code, text, retry))
        self.close_connection = True

    def handle_one_request(self):
        """Every method goes through `serve`."""
        try:
            self.raw_requestline = self.rfile.readline(65537)
            if len(self.raw_requestline) > 65536:
                return self.answer(414, "The request line is too long.")
            if not self.raw_requestline:
                self.close_connection = True
                return
            if not self.parse_request():
                return
            self.serve()
            with contextlib.suppress(OSError):
                self.wfile.flush()
        except TimeoutError:
            self.close_connection = True

    def serve(self) -> None:
        s: Service = self.server.service
        slug = self.headers.get(C.WAKE_HEADER, "")
        c = s.company(slug, self.headers.get("Host", ""))
        if c is None:
            return self.answer(404, "No Tico company at this address.")
        chunked = "chunked" in self.headers.get("Transfer-Encoding", "").lower()
        if self.headers.get("Transfer-Encoding") and not chunked:
            return self.answer(400, "Unsupported Transfer-Encoding.")
        length = None
        if not chunked and self.headers.get("Content-Length") is not None:
            text = self.headers.get("Content-Length", "").strip()
            if not text.isdigit():
                return self.answer(400, "Invalid Content-Length.")
            length = int(text)
        limit = s.body_limit(c["slug"])
        if length is not None and length > limit:
            return self.answer(413, "The request is too large.")
        upgrade = "upgrade" in self.headers.get("Connection", "").lower() and bool(self.headers.get("Upgrade"))
        state = c.get("state")
        if state == "sleeping":
            if self.command in ("GET", "HEAD") and self.path.split("?", 1)[0] == contract.HEALTH_PATH:
                return self.answer(503, "This Tico company is asleep; a visit wakes it.", 5)
            with s.held_slot(c["slug"]) as ok:
                if not ok:
                    return self.answer(503, "Too many requests are waiting; try again in a few seconds.", 5)
                a = s.attempt(c["slug"])
                if not a.done.wait(s.hold) or not a.ok:
                    return self.answer(503, C.HOLD_BODY, 5)
            c = s.make_cell().load(c["slug"]) or c
        elif state != "running":
            return self.answer(503, "This Tico company is stopped.")
        if upgrade:
            return self.answer(503, "Tico is ready; connect again.", 1)
        self.proxy(s.upstream(c), length, chunked, limit)

    # --- the request, on to the server ----------------------------------------------------------------------------

    def body(self, length: int | None, chunked: bool, limit: int):
        """The request body as it arrives, CHUNK at a time, at most `limit` bytes in all (TooLarge past it)."""
        if chunked:
            total = 0
            while True:
                line = self.rfile.readline(1024)
                size = int(line.split(b";", 1)[0].strip() or b"x", 16)
                if size == 0:
                    while self.rfile.readline(1024) not in (b"\r\n", b"\n", b""):
                        pass                # trailers: not passed on
                    return
                total += size
                if total > limit:
                    raise TooLarge
                while size:
                    data = self.rfile.read(min(size, CHUNK))
                    if not data:
                        raise ConnectionError("the request body ended early")
                    size -= len(data)
                    yield data
                self.rfile.readline(3)
        left = length or 0
        while left:
            data = self.rfile.read(min(left, CHUNK))
            if not data:
                raise ConnectionError("the request body ended early")
            left -= len(data)
            yield data

    def proxy(self, address: tuple[str, int], length: int | None, chunked: bool, limit: int) -> None:
        listed = {t.strip().lower() for t in self.headers.get("Connection", "").split(",")}
        conn = http.client.HTTPConnection(*address, timeout=PROXY_TIMEOUT)
        try:
            try:
                conn.putrequest(self.command, self.path, skip_host=True, skip_accept_encoding=True)
                for k, v in self.headers.items():
                    low = k.lower()
                    if low not in HOP and low not in listed and low != C.WAKE_HEADER.lower():
                        conn.putheader(k, v)
                if chunked:
                    conn.putheader("Transfer-Encoding", "chunked")
                elif length is not None:
                    conn.putheader("Content-Length", str(length))
                conn.putheader("Connection", "close")
                conn.endheaders()
            except OSError:
                return self.answer(503, C.HOLD_BODY, 5)
            if self.request_version == "HTTP/1.1" and \
                    self.headers.get("Expect", "").lower() == "100-continue" and (chunked or length):
                with contextlib.suppress(OSError):  # the body comes now, not after the client's own wait
                    self.wfile.write(b"HTTP/1.1 100 Continue\r\n\r\n")
                    self.wfile.flush()
            try:
                for data in self.body(length, chunked, limit):
                    conn.send(b"%x\r\n%s\r\n" % (len(data), data) if chunked else data)
                if chunked:
                    conn.send(b"0\r\n\r\n")
            except TooLarge:
                return self.answer(413, "The request is too large.")
            except ValueError:
                return self.answer(400, "Bad chunked request body.")
            except OSError:             # the server went away mid-body (or the visitor did: then nobody reads this)
                return self.answer(503, C.HOLD_BODY, 5)
            try:
                resp = conn.getresponse()
            except (OSError, http.client.HTTPException):
                return self.answer(503, C.HOLD_BODY, 5)
            self.relay(resp)
        finally:
            conn.close()

    def relay(self, resp: http.client.HTTPResponse) -> None:
        """The server's answer, streamed: its status and headers, then its body as it comes (an SSE stream too).
        HTTP/1.0 and close-delimited unless it gave a length, so no framing of ours sits between."""
        listed = {t.strip().lower() for t in (resp.getheader("Connection") or "").split(",")}
        sized = resp.getheader("Content-Length") if not resp.chunked else None
        head = [f"HTTP/1.0 {resp.status} {resp.reason}"]
        for k, v in resp.getheaders():
            low = k.lower()
            if low not in HOP and low not in listed:
                head.append(f"{k}: {v}")
        if sized is not None and sized.strip().isdigit():
            head.append(f"Content-Length: {sized.strip()}")
        head.append("Connection: close")
        self.close_connection = True
        try:
            self.wfile.write(("\r\n".join(head) + "\r\n\r\n").encode("latin-1"))
            self.wfile.flush()
            if self.command == "HEAD":
                return
            while True:
                data = resp.read1(CHUNK)
                if not data:
                    break
                self.wfile.write(data)
                self.wfile.flush()
        except (OSError, http.client.HTTPException):
            pass                        # the visitor left, or the server cut its answer: nothing more to send

    def log_message(self, *args):
        pass                            # request lines are not kept: nothing here needs them


def serve(service: Service, address: tuple[str, int] = (C.WAKE_ADDRESS, C.WAKE_PORT),
          max_connections: int = MAX_CONNECTIONS) -> Listener:
    listener = Listener(service, address, max_connections)
    threading.Thread(target=listener.serve_forever, args=(0.1,), daemon=True, name="wake-listener").start()
    return listener


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="python3 -m setup.cell_wake",
                                description="Wakes a sleeping cell company on its first request (docs/cell.md).")
    p.add_argument("--root", default=os.environ.get("TICO_CELL_ROOT") or C.ROOT)
    p.add_argument("--port", type=int, default=C.WAKE_PORT)
    args = p.parse_args(argv)
    service = Service(Path(args.root))
    stopping = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: stopping.set())
    signal.signal(signal.SIGINT, lambda *_: stopping.set())
    try:
        listener = serve(service, (C.WAKE_ADDRESS, args.port))
    except OSError as e:
        log(f"cannot listen on {C.WAKE_ADDRESS}:{args.port}: {e.strerror or e}")
        return 1
    log(f"serving {args.root} on {C.WAKE_ADDRESS}:{args.port}")
    threading.Thread(target=service.timed_wakes, args=(stopping,), name="timed-wakes", daemon=True).start()
    stopping.wait()
    listener.shutdown()
    listener.server_close()
    with service.guard:
        running = list(service.workers)
    for worker in running:              # a start under way finishes, within the unit's TimeoutStopSec
        worker.join(50)
    return 0


if __name__ == "__main__":
    with contextlib.suppress(KeyboardInterrupt):
        sys.exit(main())
