"""SCRATCH PROBE for the health-reads flake. Not for merging.

Runs the test_health_reads scenario repeatedly and records every statement of each counted call with its backend call
site and the thread that ran it. Within one iteration, the few and many measurements are compared by normalized
statement shape, so only real differences are reported.
"""

import collections
import contextlib
import os
import re
import threading
import time
import traceback

import pytest

from backend.tests.test_api import api, headers  # noqa: F401
from backend.tests.test_assigned_start import advance, cancel, clock, sweep  # noqa: F401
from backend.tests.test_status_reads import busy, setup

PATHS = ("/api/v2/health/issues", "/api/v2/health", "/api/employees")
ITERATIONS = int(os.environ.get("PROBE_ITERATIONS", "4"))
PID = os.getpid()
TAG = os.environ.get("PROBE_TAG", "")
UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")


def site():
    frames = [
        f
        for f in traceback.extract_stack(limit=60)
        if "/backend/" in f.filename and "/tests/" not in f.filename and not f.filename.endswith("/store.py")
    ]
    return " < ".join(f"{os.path.basename(f.filename)}:{f.lineno}:{f.name}" for f in frames[-3:]) or "?"


def shape(sql):
    text = UUID.sub("U", " ".join(sql.split()))
    text = re.sub(r"'[^']*'", "'S'", text)
    text = re.sub(r"\b\d+(\.\d+)?\b", "N", text)
    text = re.sub(r"(\?|'S'|U|N)(,\s*(\?|'S'|U|N))+", "L", text)
    return text[:220]


def counted(api, monkeypatch, path, token="ana-test"):
    store = api.app.state.store
    seen = []
    store.roster_rows.clear()
    with monkeypatch.context() as patched:
        for name in ("read", "read_transaction"):

            def traced(opened=getattr(store, name)):
                @contextlib.contextmanager
                def borrowed():
                    with opened() as c:
                        c.set_trace_callback(lambda q: seen.append((shape(q), site(), threading.current_thread().name)))
                        try:
                            yield c
                        finally:
                            c.set_trace_callback(None)

                return borrowed()

            patched.setattr(store, name, traced)
        response = api.get(path, headers=headers(token))
    assert response.status_code == 200, response.text
    return response.json(), seen


@pytest.mark.parametrize("iteration", range(ITERATIONS))
def test_probe(api, monkeypatch, iteration):
    started = time.monotonic()
    machine = setup(api, monkeypatch, tasks=2)
    busy(api, machine, 0, 6)
    for path in PATHS:
        counted(api, monkeypatch, path)  # warm
    few = {}
    for path in PATHS:
        _, seen = counted(api, monkeypatch, path)
        few[path] = seen
    busy(api, machine, 6, 12)
    many = {}
    for path in PATHS:
        _, seen = counted(api, monkeypatch, path)
        many[path] = seen
    for path in PATHS:
        print(
            f"PROBE pid={PID} tag={TAG} it={iteration} path={path} few={len(few[path])} many={len(many[path])} "
            f"t={time.monotonic() - started:.1f}s",
            flush=True,
        )
        a = collections.Counter(few[path])
        b = collections.Counter(many[path])
        for key, n in sorted((b - a).items()):
            print(f"PROBE   +{n} MANY-ONLY {path}: {key[0]} @ {key[1]} [{key[2]}]", flush=True)
        for key, n in sorted((a - b).items()):
            print(f"PROBE   +{n} FEW-ONLY {path}: {key[0]} @ {key[1]} [{key[2]}]", flush=True)
        threads = collections.Counter(t for _, _, t in many[path])
        print(f"PROBE   threads many {path}: {dict(threads)}", flush=True)
    assert len(many["/api/employees"]) == len(few["/api/employees"]), (
        len(few["/api/employees"]),
        len(many["/api/employees"]),
    )
