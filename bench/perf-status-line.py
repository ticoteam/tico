"""Measures the status line under a chat (thread_bot_status._step) on a synthetic running turn, before and after
step briefs (backend/step_briefs.py).

    python3 -m pytest bench/perf-status-line.py -n 0 -s -q

The turn repeats a cycle of typed deltas, a closing message and a tool call that starts and ends with an output of
1 KiB or 512 KiB, up to 100, 1,000 and 20,000 events, in an on-disk SQLite file. "before" decodes the newest 60
events whole and replays them (the code this replaced); "after" is today's `_step`. Per case it prints the median
wall time, the peak traced allocation, the characters handed to json.loads, and that both say the same word. It also
prints what keeping one event's brief costs on the write path. Synthetic data only: it shows the slope, not
production timings.
"""

import json
import sqlite3
import statistics
import time
import tracemalloc

from backend import step_briefs, thread_bot_status
from backend.tests.helpers.step_briefs import briefs_schema, replayed

SIZES = (1024, 512 * 1024)
LENGTHS = (100, 1_000, 20_000)
RUNS = 15


def cycle(n, size):
    out = "o" * size
    return [
        ("delta", {"text": "Checking "}),
        ("delta", {"text": "the failing "}),
        ("delta", {"text": "tests"}),
        ("message", {"text": "Checking the failing tests"}),
        ("tool", {"tool": "Bash", "summary": "npm test", "status": "started", "item_id": f"t{n}", "output": ""}),
        ("tool", {"tool": "Bash", "summary": "npm test", "status": "completed", "item_id": f"t{n}", "output": out}),
        ("delta", {"delta_kind": "thought", "text": "Reading the output"}),
        ("tool", {"tool": "Read", "status": "started", "item_id": f"r{n}", "output": out}),
    ]


def database(path, length, size):
    c = sqlite3.connect(path)
    c.execute(
        "CREATE TABLE attempt_events(id INTEGER PRIMARY KEY AUTOINCREMENT, attempt_id TEXT, seq INTEGER, kind TEXT, "
        "payload TEXT, created_at TEXT, UNIQUE(attempt_id,seq))"
    )
    briefs_schema(c)
    seq, n = 0, 0
    while seq < length:
        for kind, payload in cycle(n, size):
            if seq == length:
                break
            seq += 1
            stored = json.dumps(payload)
            c.execute(
                "INSERT INTO attempt_events(attempt_id,seq,kind,payload,created_at) VALUES('a',?,?,?,'t')",
                (seq, kind, stored),
            )
            step_briefs.record(c, "a", [(seq, kind, stored, payload)])
        n += 1
    c.commit()
    return c


def measure(fn, c):
    decoded = [0]
    real = json.loads

    def loads(text, *a, **k):
        decoded[0] += len(text)
        return real(text, *a, **k)

    times = []
    for _ in range(RUNS):
        start = time.perf_counter()
        fn(c, "a")
        times.append(time.perf_counter() - start)
    json.loads = loads
    tracemalloc.start()
    try:
        word = fn(c, "a")
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
        json.loads = real
    return word, statistics.median(times) * 1000, peak, decoded[0]


def test_status_line(tmp_path):
    print()
    print(f"{'output':>8} {'events':>7} {'path':>7} {'median ms':>10} {'peak KiB':>10} {'decoded KiB':>12}  word")
    for size in SIZES:
        for length in LENGTHS:
            c = database(tmp_path / f"{size}-{length}.sqlite", length, size)
            words = set()
            for name, fn in (("before", replayed), ("after", thread_bot_status._step)):
                word, ms, peak, chars = measure(fn, c)
                words.add(word)
                print(
                    f"{size // 1024:>6}Ki {length:>7} {name:>7} {ms:>10.3f} {peak / 1024:>10.1f} {chars / 1024:>12.1f}  {word}"
                )
            assert len(words) == 1
            c.close()
    payload = {"tool": "Bash", "summary": "npm test", "status": "completed", "item_id": "t1", "output": "o" * SIZES[-1]}
    stored = json.dumps(payload)
    c = database(tmp_path / "write.sqlite", 0, 0)
    times = []
    for seq in range(1, RUNS + 1):
        start = time.perf_counter()
        step_briefs.record(c, "a", [(seq, "tool", stored, payload)])
        times.append(time.perf_counter() - start)
    encode = []
    for _ in range(RUNS):
        start = time.perf_counter()
        json.dumps(payload)
        encode.append(time.perf_counter() - start)
    print(
        f"write path, one 512 KiB tool event: brief {statistics.median(times) * 1000:.3f} ms "
        f"(encoding the payload itself: {statistics.median(encode) * 1000:.3f} ms)"
    )
