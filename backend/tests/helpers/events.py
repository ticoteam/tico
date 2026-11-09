"""Helpers that other tests share, moved out of test_events.py so they import no test file."""

import http.client
import json
import queue
import socket
import threading
import time
from urllib.parse import urlparse
import pytest
import uvicorn
from backend import events as E


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
    yield (
        lambda token="ana-test", query="", headers=None, path="/api/v2/events": (
            streams.append(Stream(f"http://127.0.0.1:{port}", token, query, headers, path)) or streams[-1]
        )
    )
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
        self.conn.request("GET", path + query, headers={"Authorization": "Bearer " + token, **(headers or {})})
        self.response = self.conn.getresponse()
        self.status = self.response.status
        self.events = queue.Queue()
        self.seen = []
        self.reader = None
        if self.status == 200:
            self.reader = threading.Thread(target=self._read, daemon=True)
            self.reader.start()
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

    def until(self, match, timeout=30.0):  # a deadline, not a wait: a busy computer can take seconds per event
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
        """End the read, wait for the reader, then close: closing under a reader still in http.client's chunked read
        makes that read raise on the file it just lost."""
        try:
            self.response.fp.raw._sock.shutdown(socket.SHUT_RDWR)
        except (AttributeError, OSError):
            pass
        # shutdown wakes the reader; it owns the response until it exits. Closing underneath it races http.client.
        if self.reader:
            self.reader.join(timeout=5)
            assert not self.reader.is_alive(), "stream reader did not stop after shutdown"
        self.response.close()
        self.conn.close()


def topic(name, **fields):
    return lambda e: e["event"] == name and all(e["data"].get(k) == v for k, v in fields.items())


def ready(stream):
    return stream.until(topic("ready"))["data"]["seq"]
