"""Measures what browser streams following one conversation are sent of a bot run's steps (Perf I42, Measure 1), on a
fictional company: one run writes REPLY_STEPS reply steps and a 1 KiB, a 64 KiB and a 512 KiB tool output, and STREAMS
streams follow its conversation, as a chat page does.

    python3 -m pytest bench/perf-run-output.py -n 0 -s -q

Every stream is served from one shared snapshot, as a round of streams is (events.Rounds), first as a stream that does
not ask for compact steps (`output` left out: today's web app, `tico events tail`, a custom front end), then as one
that asks `output=compact` (the web app now). Per stream and mode it prints the largest run event in bytes, the total
run-event bytes and the server ms per change read; then, per tool output size, the bytes of that step's event.
"""

import json
import statistics
import time

from backend import events as E
from backend.store import H
from backend.tests.helpers.api import post, setup_attempt

STREAMS, REPLY_STEPS = 10, 200
TOOL_OUTPUTS = (1024, 64 * 1024, 512 * 1024)


def run_steps(api):
    """The run and its steps; returns its conversation and the change number before them."""
    r, msg, attempt = setup_attempt(api)
    aid = attempt["id"]
    post(api, f"attempts/{aid}/started", {"thread_id": "bench"}, token=r["token"])
    store = api.app.state.store
    with store.read() as c:
        start = E.latest(c)
    with store.transaction() as c:
        seq = 0
        for n in range(REPLY_STEPS):
            seq += 1
            text = {"text": f"word {n} ", "delta_kind": "text"}
            c.execute(
                "INSERT INTO attempt_events(attempt_id,seq,kind,payload,created_at) VALUES(?,?,?,?,?)",
                (aid, seq, "delta", json.dumps(text), H.now()),
            )
        for size in TOOL_OUTPUTS:
            seq += 1
            tool = {"name": "bash", "output": "x" * size}
            c.execute(
                "INSERT INTO attempt_events(attempt_id,seq,kind,payload,created_at) VALUES(?,?,?,?,?)",
                (aid, seq, "tool", json.dumps(tool), H.now()),
            )
    return msg["conversation_id"], start


def serve(api, cid, start, compact):
    """Every stream's run events from one snapshot: per stream (largest, total bytes, ms per change, tool steps)."""
    store, auth = api.app.state.store, api.app.state.auth
    ana = auth.settings.test_identities["ana-test"]
    report = []
    with store.read() as c:
        c.execute("BEGIN")
        try:
            snapshot = E._Snapshot(c, [E._Poll(None, None, start, ()) for _ in range(STREAMS)])
            for _ in range(STREAMS):
                E._between_viewers(c)
                after, sizes, tools, changes, spent = start, [], {}, 0, 0.0
                while True:
                    began = time.perf_counter()
                    events, next_after, reset, more = E.read_in(
                        c,
                        snapshot,
                        auth,
                        ana,
                        after,
                        topics=("runs",),
                        conversations=(cid,),
                        task_views=lambda rows, c, who, visible_sql="1": [],
                        task_view=None,
                        compact_runs=compact,
                    )
                    encoded = [(data, len(data.encoded.encode())) for _, topic, data in events if topic == "runs"]
                    spent += time.perf_counter() - began
                    assert not reset
                    changes += c.execute(
                        "SELECT COUNT(*) FROM changes WHERE seq>? AND seq<=? AND topic='runs'", (after, next_after)
                    ).fetchone()[0]
                    for data, size in encoded:
                        sizes.append(size)
                        output = data.get("output") or {}
                        if output.get("kind") == "tool":
                            tools[output["seq"] - REPLY_STEPS - 1] = size
                    after = next_after
                    if not more:
                        break
                report.append((max(sizes), sum(sizes), 1000 * spent / max(changes, 1), tools))
        finally:
            c.rollback()
    return report


def test_run_event_bytes_per_browser_stream(api):
    cid, start = run_steps(api)
    print()
    print(f"one run: {REPLY_STEPS} reply steps and tool outputs of {', '.join(str(s) for s in TOOL_OUTPUTS)} bytes; ")
    print(f"{STREAMS} streams following its conversation")
    results = {}
    for name, compact in (("full (today)", False), ("compact", True)):
        report = serve(api, cid, start, compact)
        results[name] = report
        print(f"\n{name}")
        print(f"{'stream':>6} {'largest':>9} {'total':>10} {'ms/change':>10}")
        for n, (largest, total, ms, _) in enumerate(report):
            print(f"{n:>6} {largest:>9} {total:>10} {ms:>10.3f}")
        print(
            f"median largest={statistics.median(r[0] for r in report)} total={statistics.median(r[1] for r in report)} "
            f"ms/change={statistics.median(r[2] for r in report):.3f}"
        )
    print("\nbytes of one tool output step's event, per stream")
    print(f"{'output':>8} {'full':>9} {'compact':>8} {'cut':>7}")
    full, compact = results["full (today)"][0][3], results["compact"][0][3]
    for n, size in enumerate(TOOL_OUTPUTS):
        print(f"{size:>8} {full[n]:>9} {compact[n]:>8} {100 * (1 - compact[n] / full[n]):>6.2f}%")
    assert max(r[0] for r in results["compact"]) < 2048
