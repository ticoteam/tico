#!/usr/bin/env python3
"""Stands in for a provider CLI while a replay is recorded (runner-rs src/hosts/replay.rs, `record_replays`).

    replay_proxy.py <log> <real cli> [args...]

Runs the real CLI, passes stdin and stdout through line by line, and appends what crosses the process boundary
to <log>, in the order seen: a `spawn` entry, each line the runner wrote (`out`) and the CLI wrote (`in`),
`close` when the runner closed stdin, `signal` when it sent SIGTERM (passed on to the CLI), and `exit` with the
return code and stderr. Probes (`--version`, `--help`) run straight through, unrecorded. See runner-rs/tests/fixtures/REPLAY.md.
"""

import json
import signal
import subprocess
import sys
import threading


def main():
    log_path, argv = sys.argv[1], sys.argv[2:]
    if len(argv) == 2 and argv[1] in ("--version", "--help"):
        sys.exit(subprocess.call(argv))
    lock = threading.RLock()  # the SIGTERM handler records from the main thread
    log = open(log_path, "a", encoding="utf-8")

    def record(entry):
        with lock:
            log.write(json.dumps(entry) + "\n")
            log.flush()

    def line_entry(direction, raw):
        text = raw.decode("utf-8", "replace").rstrip("\n")
        try:
            return {"dir": direction, "frame": json.loads(text)}
        except ValueError:
            return {"dir": direction, "line": text}

    record({"dir": "spawn"})
    child = subprocess.Popen(argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)

    def forward(signum, _frame):
        record({"dir": "signal", "signal": signal.Signals(signum).name[3:]})
        child.send_signal(signum)

    signal.signal(signal.SIGTERM, forward)

    def pump_in():
        for raw in iter(sys.stdin.buffer.readline, b""):
            if raw.strip():
                record(line_entry("out", raw))
            try:
                child.stdin.write(raw)
                child.stdin.flush()
            except OSError:
                break
        record({"dir": "close"})
        try:
            child.stdin.close()
        except OSError:
            pass

    stderr = []
    threading.Thread(target=pump_in, daemon=True).start()
    err = threading.Thread(target=lambda: stderr.append(child.stderr.read()), daemon=True)
    err.start()
    for raw in iter(child.stdout.readline, b""):
        if raw.strip():
            record(line_entry("in", raw))
        try:
            sys.stdout.buffer.write(raw)
            sys.stdout.buffer.flush()
        except OSError:
            pass
    code = child.wait()
    err.join(5)
    record({"dir": "exit", "code": code, "stderr": b"".join(stderr).decode("utf-8", "replace")[-2000:]})
    sys.exit(code if code >= 0 else 128 - code)


if __name__ == "__main__":
    main()
