#!/usr/bin/env python3
"""Comparable local screen relay measurements with fictional data and owned loopback services.

Use the development venv and an output directory on the task disk. No live installation or Docker
resources are accessed. Client scheduling is included in RTT; this is not an isolated server CPU test.
"""
import argparse
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack
import json
import os
from pathlib import Path
import platform
import statistics
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import conftest  # noqa: E402,F401  isolates registry and AWS credentials just as the contract tests do
from backend.tests.test_screen_relay_contract import running_service  # noqa: E402


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--rust-bin", type=Path, required=True)
    parser.add_argument("--computers", type=int, default=10)
    parser.add_argument("--sessions", type=int, default=3)
    parser.add_argument("--rounds", type=int, default=100)
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--bytes", type=int, default=65536)
    args = parser.parse_args()
    if not (1 <= args.sessions <= 3 and args.computers > 0 and args.rounds > 0 and args.repeats > 0
            and 1 <= args.bytes <= 16 << 20):
        parser.error("positive counts, 1–3 sessions per computer and payloads up to 16 MiB are required")
    args.output.mkdir(parents=True, exist_ok=False)
    result = {"platform": platform.platform(), "python": sys.version, "cpu_count": os.cpu_count(),
              "parameters": {k: str(v) if isinstance(v, Path) else v for k, v in vars(args).items()}, "samples": []}
    payload = b"x" * args.bytes
    for repeat in range(args.repeats):
        for engine in ["rust"]:
            root = args.output / f"{engine}-{repeat}"
            root.mkdir()
            with running_service(root, args.rust_bin.resolve()) as service, ExitStack() as sockets:
                pairs = []
                for _ in range(args.computers):
                    computer = service.computer()
                    for _ in range(args.sessions):
                        opened = service.opened(computer)
                        browser = sockets.enter_context(service.websocket(opened["ws_path"]))
                        runner = sockets.enter_context(service.websocket(service.runner_path(opened), token=computer["token"]))
                        pairs.append((browser, runner))
                def exchange(pair):
                    browser, runner = pair
                    latencies = []
                    for _ in range(args.rounds):
                        started = time.perf_counter()
                        browser.send(payload)
                        assert runner.recv(timeout=10) == payload
                        runner.send(payload)
                        assert browser.recv(timeout=10) == payload
                        latencies.append((time.perf_counter() - started) * 1000)
                    return latencies
                before, started = os.getloadavg(), time.perf_counter()
                with ThreadPoolExecutor(len(pairs)) as pool:
                    latencies = sorted(t for sample in pool.map(exchange, pairs) for t in sample)
                seconds = time.perf_counter() - started
                sample = {"engine": engine, "repeat": repeat, "seconds": seconds, "round_trips": len(latencies),
                          "p50_ms": statistics.median(latencies), "p95_ms": latencies[int(.95 * (len(latencies) - 1))],
                          "relay_mib_per_second": 2 * len(latencies) * args.bytes / (1 << 20) / seconds,
                          "load_before": before, "load_after": os.getloadavg()}
                result["samples"].append(sample)
                (args.output / "results.json").write_text(json.dumps(result, indent=2) + "\n")
                print(json.dumps(sample), flush=True)


if __name__ == "__main__":
    main()
