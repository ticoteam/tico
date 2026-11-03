"""Temporary probe (not for merge): which call sites of /api/v2/health and /api/status issue statements that grow."""
import collections
import contextlib
import os
import sys

from backend.store import H
from backend.tests.test_api import api  # noqa: F401  (the fixture)
from backend.tests.test_api import assign, headers, post, runner
from backend.tests.test_claim_statements import claim, handed_run, seed
from backend.tests.test_health_statements import loop, stall


def site():
    frames, f = [], sys._getframe(2)
    while f is not None and len(frames) < 3:
        name = f.f_code.co_filename
        if "/backend/" in name and "/backend/tests/" not in name:
            frames.append(f"{os.path.basename(name)}:{f.f_code.co_name}:{f.f_lineno}")
        f = f.f_back
    return " < ".join(frames) or "?"


@contextlib.contextmanager
def profiled(api, sites, texts):
    store = api.app.state.store
    original = store.read

    @contextlib.contextmanager
    def borrowed():
        with original() as c:
            def trace(sql):
                key = site()
                sites[key] += 1
                texts.setdefault(key, sql)
            c.set_trace_callback(trace)
            try:
                yield c
            finally:
                c.set_trace_callback(None)
    store.read = borrowed
    try:
        yield
    finally:
        del store.read


def measure(api, route):
    sites, texts = collections.Counter(), {}
    path = "/api/v2/health" if route == "health" else "/api/status"
    with profiled(api, sites, texts):
        response = api.get(path, headers=headers())
    assert response.status_code == 200, response.text
    return sum(sites.values()), sites, texts


def offline(api, prefix, count):
    """`count` bots on a computer that never reported in, each with a queued chat."""
    machine = runner(api, label=f"Offline {prefix}")
    slugs = [f"{prefix}-{n:02d}" for n in range(count)]
    store = api.app.state.store
    with store.transaction() as c:
        H.sync_registry(c, {s: {"name": s, "runtime": "fake", "status": "active"} for s in slugs},
                        {"people": [{"id": p, "email": p + "@acme.example"} for p in ("ana", "ben")]})
        for s in slugs:
            c.execute("INSERT INTO bot_config(bot,config,owner_id) VALUES(?,?,'ana')",
                      (s, '{"name": "%s", "runtime": "fake"}' % s))
    for s in slugs:
        assign(api, machine, s)
    for s in slugs:
        post(api, "chat/" + s, {"text": "Please summarize the current work."})


def work(api, machine, rounds):
    """Real claims and completions: attempts from the last day for the machine's bots."""
    for _ in range(rounds):
        answer, _ = claim(api, machine)
        if not answer.get("attempt"):
            break
        handed_run(api, machine, answer)


def report(route, small, large):
    small_total, small_sites, _ = small
    large_total, large_sites, _ = large
    print(f"\nROUTE {route}: total small={small_total} large={large_total} delta={large_total - small_total}", flush=True)
    deltas = sorted(((large_sites[k] - small_sites[k], k) for k in set(small_sites) | set(large_sites)), reverse=True)
    for delta, key in deltas[:45]:
        if delta:
            print(f"  +{delta:4d}  small={small_sites[key]:4d} large={large_sites[key]:4d}  {key}", flush=True)


def test_probe(api):
    store_api = api
    seed(api, "few", 6)
    stall(api, 3, "a", blocked=1)
    loop(api, 3)
    for route in ("health", "status"):
        measure(api, route)
    small = {route: measure(api, route) for route in ("health", "status")}
    machine = seed(api, "many", 60)
    work(api, machine, 20)
    offline(api, "off", 30)
    stall(api, 100, "b", blocked=20)
    loop(api, 20, start=3)
    for route in ("health", "status"):
        measure(api, route)
    large = {route: measure(api, route) for route in ("health", "status")}
    for route in ("health", "status"):
        report(route, small[route], large[route])
        small_total, small_sites, _ = small[route]
        large_total, large_sites, large_texts = large[route]
        growing = [k for k in large_sites if large_sites[k] - small_sites[k] >= 5]
        with store_api.app.state.store.read() as c:
            for key in sorted(growing, key=lambda k: large_sites[k] - small_sites[k], reverse=True)[:40]:
                sql = large_texts.get(key, "")
                if not sql.lstrip().upper().startswith(("SELECT", "WITH")):
                    print("  NONSELECT", key, sql[:120].replace("\n", " "), flush=True)
                    continue
                try:
                    detail = " | ".join(str(r[-1]) for r in c.execute("EXPLAIN QUERY PLAN " + sql).fetchall())
                except Exception as error:      # noqa: BLE001
                    detail = f"explain failed: {error}"
                print("  PLAN", key, "::", detail[:400], flush=True)
                print("     SQL", sql[:300].replace("\n", " "), flush=True)
