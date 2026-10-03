#!/usr/bin/env python3
"""Run every Python and browser release check; a green run must finish in under 300 seconds.

Use the test environment's Python to invoke this script. Existing pytest and
TICO_UI_JOBS settings still select concurrency; no tests are filtered out.
"""
import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
import subprocess
import sys
import time


ROOT = Path(__file__).resolve().parents[1]
BUDGET_SECONDS = 300


def load():
    return ','.join(f'{value:.2f}' for value in os.getloadavg()) if hasattr(os, 'getloadavg') else 'unavailable'


def main():
    started, initial_load = time.monotonic(), load()
    env = {**os.environ, 'TICO_PYTHON': sys.executable}
    # Both suites use independent fixtures. Reserve roughly half the CPUs for browser servers.
    env.setdefault('PYTEST_XDIST_AUTO_NUM_WORKERS', str(max(1, (os.cpu_count() or 2) // 2)))
    commands = (
        ('Python', [sys.executable, '-m', 'pytest', '-q']),
        ('Browser', ['node', 'scripts/ui-tests.cjs']),
    )

    def run(name, command):
        before = time.monotonic()
        result = subprocess.run(command, cwd=ROOT, env=env, capture_output=True, text=True)
        return name, result, time.monotonic() - before

    failed = False
    with ThreadPoolExecutor(max_workers=2) as pool:
        pending = [pool.submit(run, name, command) for name, command in commands]
        for future in as_completed(pending):
            name, result, duration = future.result()
            print(result.stdout, end='', flush=True)
            print(result.stderr, end='', file=sys.stderr, flush=True)
            print(f'{name} checks: exit {result.returncode}, {duration:.2f}s', flush=True)
            failed = failed or result.returncode != 0
    if failed:
        print(f'Release checks failed after {time.monotonic() - started:.2f}s; load {initial_load} -> {load()}', flush=True)
        return 1
    elapsed = time.monotonic() - started
    within_budget = elapsed < BUDGET_SECONDS
    print(f'Release checks {"passed" if within_budget else "exceeded budget"}: {elapsed:.2f}s / '
          f'{BUDGET_SECONDS}s; load {initial_load} -> {load()}', flush=True)
    return 0 if within_budget else 1


if __name__ == '__main__':
    raise SystemExit(main())
