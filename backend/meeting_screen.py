"""Ephemeral, bounded screen frames for people in a live meeting. Nothing is persisted."""
import asyncio
import hashlib
import threading
import time
import uuid
from dataclasses import dataclass

from fastapi import Request
from fastapi.responses import Response

from .store import Problem
from .views import human_only

MAX_BYTES = 1_000_000
MAX_EDGE = 1920
TTL = 10
MAX_SHARES = 32
HEADERS = {"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"}


def jpeg_size(data):
    """Read bounded JPEG markers without decoding attacker-controlled pixels."""
    if not data.startswith(b"\xff\xd8") or not data.endswith(b"\xff\xd9"):
        raise Problem("screen_image", "A JPEG image is required", 422)
    pos = 2
    while pos + 4 <= len(data):
        if data[pos] != 255:
            break
        while pos < len(data) and data[pos] == 255:
            pos += 1
        if pos >= len(data):
            break
        marker = data[pos]
        pos += 1
        if marker in (0xD8, 0xD9, 0xDA):
            break
        if 0xD0 <= marker <= 0xD7 or marker == 1:
            continue
        length = int.from_bytes(data[pos:pos + 2], "big")
        if length < 2 or pos + length > len(data):
            break
        if marker in (0xC0, 0xC1, 0xC2):
            if length < 8:
                break
            height = int.from_bytes(data[pos + 3:pos + 5], "big")
            width = int.from_bytes(data[pos + 5:pos + 7], "big")
            if 0 < width <= MAX_EDGE and 0 < height <= MAX_EDGE:
                return width, height
            raise Problem("screen_image", "Screen dimensions exceed the limit", 422)
        pos += length
    raise Problem("screen_image", "Invalid JPEG header", 422)


@dataclass
class Share:
    generation: str
    request_id: str
    alive: float
    owner: str
    seq: int = 0
    frame: bytes = b""


class Frames:
    def __init__(self):
        self.lock = threading.RLock()
        self.shares = {}

    async def run(self, stop):
        try:
            while not stop.is_set():
                await asyncio.to_thread(self.expire)
                try:
                    await asyncio.wait_for(stop.wait(), timeout=1)
                except asyncio.TimeoutError:
                    pass
        finally:
            await asyncio.to_thread(self.clear)

    def expire(self):
        with self.lock:
            self.prune()

    def clear(self):
        with self.lock:
            self.shares.clear()

    def prune(self):
        now = time.monotonic()
        self.shares = {rid: s for rid, s in self.shares.items() if now - s.alive < TTL}

    def stop(self, rid):
        with self.lock:
            self.shares.pop(rid, None)

    def current(self, rid, generation=None):
        self.prune()
        share = self.shares.get(rid)
        if share is None:
            raise Problem("screen_stopped", "Screen sharing has stopped", 410)
        if generation is not None and generation != share.generation:
            raise Problem("screen_generation", "This screen share has been replaced", 409)
        return share


def install(app, store, auth, require_read, connector):
    frames = Frames()
    app.state.meeting_frames = frames
    prefix = "/api/v2/live-meetings/{rid}/screen"

    def check(request, rid, publishing=False):
        who = request.state.identity
        human_only(who)
        auth.domain(who)
        with store.read() as c:
            row = connector(c, auth, who, rid) if publishing else require_read(c, auth, who, rid)
            if row["state"] != "live":
                frames.stop(rid)
                raise Problem("screen_stopped", "Screen sharing requires a live meeting", 410)
            return row

    def audience(rid):
        with store.read() as c:
            row = c.execute("SELECT visibility,owner_actor FROM live_meetings WHERE id=?", (rid,)).fetchone()
            actors = [] if row["visibility"] == "team" else c.execute("SELECT actor FROM live_meeting_access WHERE meeting_id=? UNION SELECT actor FROM live_meeting_humans WHERE meeting_id=? ORDER BY actor", (rid, rid)).fetchall()
            return hashlib.sha256(repr((tuple(row), [a[0] for a in actors])).encode()).hexdigest()

    @app.get(prefix)
    def capability(request: Request, rid: str):
        row = check(request, rid, True)
        return {"visibility": row["visibility"], "limits": {"max_bytes": MAX_BYTES, "max_edge": MAX_EDGE, "fps": 5}, "supported": True, "audience": audience(rid)}

    @app.get(prefix + "/status")
    def status(request: Request, rid: str):
        check(request, rid)
        with frames.lock:
            share = frames.current(rid)
            return Response(content='{"generation":"' + share.generation + '"}', media_type="application/json", headers=HEADERS)

    @app.post(prefix + "/start")
    def start(request: Request, rid: str, expected_audience: str = ""):
        request_id = request.headers.get("Idempotency-Key", "")
        if not 1 <= len(request_id) <= 200:
            raise Problem("screen_request", "A screen start request ID is required", 422)
        # The database read and memory change share one lock order with every frame operation.
        with frames.lock:
            row = check(request, rid, True)
            if expected_audience and expected_audience != audience(rid):
                raise Problem("screen_audience", "The meeting audience changed; confirm screen sharing again", 409)
            frames.prune()
            previous = frames.shares.get(rid)
            if previous and previous.request_id == request_id:
                return {"generation": previous.generation}
            if rid not in frames.shares and len(frames.shares) >= MAX_SHARES:
                raise Problem("screen_capacity", "Too many screen shares; try again later", 429)
            if rid not in frames.shares and sum(s.owner == row["owner_actor"] for s in frames.shares.values()) >= 2:
                raise Problem("screen_capacity", "Stop another screen share before starting one here", 429)
            share = Share(str(uuid.uuid4()), request_id, time.monotonic(), row["owner_actor"])
            frames.shares[rid] = share
            return {"generation": share.generation}

    @app.post(prefix + "/frame")
    async def publish(request: Request, rid: str, generation: str, seq: int):
        await asyncio.to_thread(check, request, rid, True)
        if request.headers.get("content-type", "").split(";")[0] != "image/jpeg":
            raise Problem("screen_image", "Content-Type must be image/jpeg", 415)
        data = bytearray()
        async for chunk in request.stream():
            if len(data) + len(chunk) > MAX_BYTES:
                raise Problem("too_large", "Screen frame exceeds the limit", 413)
            data.extend(chunk)
        def accept():
            jpeg_size(data)
            check(request, rid, True)
            with frames.lock:
                share = frames.current(rid, generation)
                if not share.seq < seq <= 2**53 - 1:
                    raise Problem("screen_sequence", "Screen frame is out of order", 409)
                share.frame, share.seq, share.alive = bytes(data), seq, time.monotonic()
        await asyncio.to_thread(accept)
        return Response(status_code=204, headers=HEADERS)

    @app.post(prefix + "/alive")
    def alive(request: Request, rid: str, generation: str):
        check(request, rid, True)
        with frames.lock:
            frames.current(rid, generation).alive = time.monotonic()
        return Response(status_code=204, headers=HEADERS)

    @app.post(prefix + "/stop")
    def stop(request: Request, rid: str, generation: str):
        human_only(request.state.identity)
        with store.read() as c:
            connector(c, auth, request.state.identity, rid)
        with frames.lock:
            share = frames.shares.get(rid)
            # A delayed stop must never kill a newer generation.
            if share and share.generation == generation:
                frames.stop(rid)
        return Response(status_code=204, headers=HEADERS)

    def poll_frame(request, rid, after, recheck):
        # This function always runs on a worker. Neither SQLite nor a contended native lock
        # can block the ASGI event loop, including while a lifecycle write is committing.
        with frames.lock:
            share = frames.current(rid)
            version = f"{share.generation}:{share.seq}"
            changed = bool(share.frame and version != after)
        if recheck or changed:
            check(request, rid)
        with frames.lock:
            share = frames.current(rid)
            version = f"{share.generation}:{share.seq}"
            return (share.frame, version) if share.frame and version != after else None

    @app.get(prefix + "/frame")
    async def read(request: Request, rid: str, after: str = "", wait: float = 12):
        # Authorize before revealing whether a share exists, even for a stopped meeting.
        await asyncio.to_thread(check, request, rid)
        deadline = time.monotonic() + max(0, min(wait, 15))
        checked = time.monotonic()
        while True:
            recheck = time.monotonic() - checked >= 1
            frame = await asyncio.to_thread(poll_frame, request, rid, after, recheck)
            if recheck:
                checked = time.monotonic()
            if frame:
                data, version = frame
                return Response(data, media_type="image/jpeg", headers={**HEADERS,
                    "ETag": f'"{version}"', "X-Screen-Version": version})
            if time.monotonic() >= deadline or await request.is_disconnected():
                return Response(status_code=204, headers=HEADERS)
            began = time.perf_counter()
            try:
                await asyncio.sleep(.2)
            finally:
                # Intentional idle waiting is not server processing; loop stalls still count.
                request.state.idle_wait_ms = getattr(request.state, "idle_wait_ms", 0) + min(200, (time.perf_counter() - began) * 1000)
