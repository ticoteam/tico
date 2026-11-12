"""Measures how much one person's live-events read prepares when they are behind on large run outputs (task I49), on a
fictional company.

    .venv/bin/python -m pytest bench/perf-live-events.py -n 0 -s -q

Measure 1: one viewer drains OUTPUTS run outputs of OUTPUT_BYTES each from one run they may read, in reads of
ROUND_ROWS changes, as a round reads. It prints the reads, the payload decodes (H._json calls given a stored payload
text), the bytes decoded, the bytes sent and the median ms per read. Target: at most one decode per output sent, and no
more than 2 x bytes sent + 1 MiB decoded.

Measure 2: the same backlog's first read, then ten light viewers in the same round, each following one conversation
with one small new message. It prints the light viewers' waits from the round's start, with and without the heavy
viewer ahead of them. Target: no longer than without it, plus one read of READ_BYTES.
"""

import contextlib
import json
import statistics
import time

from backend import events as E
from backend import hubdb as H
from backend.auth import Identity
from backend.tests.helpers.api import post, setup_attempt

OUTPUTS = 100
OUTPUT_BYTES = 64 * 1024
LIGHT = 10
MiB = 1024 * 1024
ANA = Identity("human:ana", "human", "ana@acme.example")


def payload(n):
    text = json.dumps({"delta_kind": "text", "text": ""})
    return json.dumps({"delta_kind": "text", "text": f"{n:04d}" + "x" * (OUTPUT_BYTES - len(text) - 4)})


@contextlib.contextmanager
def decodes():
    """Each H._json call given a stored run output's text: [(characters)]."""
    seen, original = [], H._json

    def counted(value, default=None):
        if isinstance(value, str) and len(value) >= OUTPUT_BYTES // 2:
            seen.append(len(value))
        return original(value, default)

    H._json = counted
    try:
        yield seen
    finally:
        H._json = original


def backlog(api):
    """A run of ana's with OUTPUTS large outputs waiting after the returned cursor."""
    store = api.app.state.store
    _, msg, attempt = setup_attempt(api)
    with store.read() as c:
        cursor = E.latest(c)
    with store.transaction() as c:
        for n in range(OUTPUTS):
            c.execute(
                "INSERT INTO attempt_events(attempt_id,seq,kind,payload,created_at) VALUES(?,?,'delta',?,?)",
                (attempt["id"], 1000 + n, payload(n), H.now()),
            )
    return cursor, msg["conversation_id"]


def read(c, snapshot, cursor, **kw):
    return E.read_in(c, snapshot, api_auth, ANA, cursor, limit=E.ROUND_ROWS, **kw)


api_auth = None


def test_one_viewer_drains_large_run_outputs(api):
    global api_auth
    api_auth = api.app.state.auth
    store = api.app.state.store
    cursor, _ = backlog(api)
    reads, sent, walls, outputs = 0, 0, [], []
    with decodes() as seen:
        more = True
        while more:
            with store.read() as c:
                c.execute("BEGIN")
                start = time.perf_counter()
                events, cursor, reset, more = read(c, E._Snapshot(c), cursor, topics=("runs",))
                walls.append(time.perf_counter() - start)
                c.rollback()
            assert not reset
            reads += 1
            sent += sum(len(data.encoded) for _, _, data in events)
            outputs += [data["output"]["payload"]["text"][:4] for _, _, data in events if "output" in data]
    assert outputs == [f"{n:04d}" for n in range(OUTPUTS)], "every output, once, in order"
    print(
        f"\nMeasure 1: reads={reads} decodes={len(seen)} decoded_mb={sum(seen) / 1e6:.1f} sent_mb={sent / 1e6:.1f} "
        f"median_ms_per_read={statistics.median(walls) * 1000:.1f} "
        f"target: decodes<={OUTPUTS} decoded_mb<={(2 * sent + MiB) / 1e6:.1f}",
        flush=True,
    )


def test_light_viewers_wait_behind_one_heavy_viewer(api):
    global api_auth
    api_auth = api.app.state.auth
    store = api.app.state.store
    heavy_cursor, _ = backlog(api)
    with store.read() as c:
        light_cursor = E.latest(c)
    rooms = [post(api, "chat/ops", {"text": f"Quick question {n}"})["conversation_id"] for n in range(LIGHT)]

    class Poll:
        scope = ()

        def __init__(self, cursor):
            self.cursor = cursor

    def round_(heavy):
        """(the last light viewer's wait from the round's start, the heavy viewer's own read in that round)."""
        polls = ([Poll(heavy_cursor)] if heavy else []) + [Poll(light_cursor) for _ in rooms]
        waits, prepare = [], 0.0
        with store.read() as c:
            c.execute("BEGIN")
            start = time.perf_counter()
            snapshot = E._Snapshot(c, polls)
            if heavy:
                began = time.perf_counter()
                E._between_viewers(c)
                read(c, snapshot, heavy_cursor, topics=("runs",))
                prepare = time.perf_counter() - began
            for room in rooms:
                E._between_viewers(c)
                events, _, _, _ = read(c, snapshot, light_cursor, topics=("messages",), conversations=(room,))
                assert events, "each light viewer gets its message"
                waits.append(time.perf_counter() - start)
            c.rollback()
        return max(waits), prepare

    round_(False), round_(True)  # warm both before timing
    rounds, lone, timed = 15, [], []
    for _ in range(rounds):  # alternated, so drift on a shared computer falls on both alike
        lone.append(round_(False)[0])
        timed.append(round_(True))
    alone = statistics.median(lone)
    behind = statistics.median(wait for wait, _ in timed)
    prepare = statistics.median(read_ for _, read_ in timed)
    print(
        f"\nMeasure 2: last light viewer's wait alone_ms={alone * 1000:.1f} behind_heavy_ms={behind * 1000:.1f} "
        f"heavy_read_in_round_ms={prepare * 1000:.1f} target: behind<=alone+one_read={(alone + prepare) * 1000:.1f}",
        flush=True,
    )
