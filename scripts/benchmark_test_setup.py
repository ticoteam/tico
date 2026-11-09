#!/usr/bin/env python3
"""Compare the same API tests with fresh and worker-local snapshot databases.

Run with the development venv's Python. Results include command, dependency-lock hash, load and
every sample; alternating order limits warm-cache and background-load bias on shared computers.
This is a targeted benchmark, not the shared release journey.
"""

import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import statistics
import subprocess
import sys
import time


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--workers", nargs="+", default=["2", "4", "6", "auto"])
    parser.add_argument("--rounds", type=int, default=3)
    parser.add_argument("tests", nargs="*", default=["backend/tests/test_api.py"])
    args = parser.parse_args()
    if args.rounds < 1:
        parser.error("rounds must be positive")
    args.output.mkdir(parents=True, exist_ok=False)
    result = {
        "python": sys.version,
        "platform": platform.platform(),
        "cpu_count": os.cpu_count(),
        "requirements_sha256": hashlib.sha256(Path("backend/requirements-dev.txt").read_bytes()).hexdigest(),
        "samples": [],
    }
    for repeat in range(args.rounds):
        for workers in args.workers:
            for fresh in [True, False] if repeat % 2 == 0 else [False, True]:
                name = f"{'fresh' if fresh else 'snapshot'}-{workers}-{repeat}"
                command = [
                    sys.executable,
                    "-m",
                    "pytest",
                    "-q",
                    "-n",
                    workers,
                    *args.tests,
                    "--durations=10",
                    "--basetemp=" + str(args.output / name),
                ]
                command.append("--fresh-api-databases" if fresh else "--api-database-snapshots")
                before, started = os.getloadavg(), time.perf_counter()
                with (args.output / (name + ".log")).open("w") as log:
                    process = subprocess.run(command, stdout=log, stderr=subprocess.STDOUT)
                result["samples"].append(
                    {
                        "workers": workers,
                        "fresh": fresh,
                        "repeat": repeat,
                        "seconds": time.perf_counter() - started,
                        "load_before": before,
                        "load_after": os.getloadavg(),
                        "command": command,
                        "returncode": process.returncode,
                    }
                )
                (args.output / "results.json").write_text(json.dumps(result, indent=2) + "\n")
                if process.returncode:
                    raise SystemExit(f"Benchmark failed; see {args.output / (name + '.log')}")
    for workers in args.workers:
        medians = [
            statistics.median(
                s["seconds"] for s in result["samples"] if s["workers"] == workers and s["fresh"] == fresh
            )
            for fresh in (True, False)
        ]
        print(
            f"{workers} workers: fresh {medians[0]:.3f}s, snapshot {medians[1]:.3f}s, "
            f"{100 * (1 - medians[1] / medians[0]):.1f}% reduction"
        )


if __name__ == "__main__":
    main()
