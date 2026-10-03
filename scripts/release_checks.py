#!/usr/bin/env python3
"""Run every Python and browser release check; a green run must finish in under 300 seconds.

Use the test environment's Python to invoke this script. Existing pytest and
TICO_UI_JOBS settings still select concurrency; no tests are filtered out.
"""
import os
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
    for name, command in (
            ('Python', [sys.executable, '-m', 'pytest', '-q']),
            ('Browser', ['node', 'scripts/ui-tests.cjs'])):
        before = time.monotonic()
        result = subprocess.run(command, cwd=ROOT, env=env)
        print(f'{name} checks: exit {result.returncode}, {time.monotonic() - before:.2f}s', flush=True)
        if result.returncode:
            print(f'Release checks failed after {time.monotonic() - started:.2f}s; load {initial_load} -> {load()}', flush=True)
            return 1
    elapsed = time.monotonic() - started
    within_budget = elapsed < BUDGET_SECONDS
    print(f'Release checks {"passed" if within_budget else "exceeded budget"}: {elapsed:.2f}s / '
          f'{BUDGET_SECONDS}s; load {initial_load} -> {load()}', flush=True)
    return 0 if within_budget else 1


if __name__ == '__main__':
    raise SystemExit(main())
