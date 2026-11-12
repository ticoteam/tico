"""Measures how the Rust runner uploads a turn's streamed events (perf I07): 1, 10 and 30 synthetic turns at once on
one runner, each streaming the same reply, against a synthetic hub with one write lock.

    python3 bench/perf-event-uploads.py              # 2,000 deltas a turn, 10 ms apart, 3 rounds (about 6 min)
    python3 bench/perf-event-uploads.py 500 1,30 5   # 500 deltas a turn, at 1 and 30 turns, 5 rounds

It runs the ignored `upload_pace::streamed::upload_bench` test in runner-rs (release build), which drives the
runner's real stream loop and `Runner::flush`, plus the drive loop's 1 s flush and input poll, against an
in-process hub that stores events as backend/execution.py `events` does and holds one lock for each upload
(3 ms plus 15 us an event) and input poll (1.5 ms). Nothing touches a real Tico server or team data.

Each round runs every size twice: "fixed" is the runner before I07 (first event at once, then one upload per 50 ms
whatever the load, each flush reading and acknowledging on the store's one connection), "adaptive" the current
pacing (runner-rs/src/upload_pace.rs). Which goes first alternates by round and size; the output ends with the
median of each column over the rounds. Per row: event uploads per turn, event
upload p95 and input poll p95 (time in the hub, lock wait included), time from a turn's first delta being
recorded to reaching the hub (p50, p95), and the same for control events (status, tool, the final message), p95.
The two "w" columns measure first text and control events again from their row's commit to the local outbox, not
from the event: pacing and the post alone, without the local disk write.
Every run also checks that the hub stored each turn's events exactly as the runner recorded them.

The hub's costs are synthetic: the numbers show the mechanism and its slope with concurrency, not production p95.
"""

import os
import subprocess
import sys
from pathlib import Path

RUNNER = Path(__file__).resolve().parent.parent / "runner-rs"


def main(argv):
    env = dict(os.environ)
    if len(argv) > 1:
        env["TICO_BENCH_DELTAS"] = argv[1]
    if len(argv) > 2:
        env["TICO_BENCH_TURNS"] = argv[2]
    if len(argv) > 3:
        env["TICO_BENCH_RUNS"] = argv[3]
    command = [
        "cargo",
        "test",
        "--release",
        "--locked",
        "upload_pace::streamed::upload_bench",
        "--",
        "--ignored",
        "--nocapture",
        "--test-threads=1",
    ]
    return subprocess.call(command, cwd=RUNNER, env=env)


if __name__ == "__main__":
    sys.exit(main(sys.argv))
