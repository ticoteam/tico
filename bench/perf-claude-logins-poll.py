"""Measures `POST /api/v2/runner-claude-logins`, the poll each computer sends for its full Claude logins every few minutes
(backend/claude_login.py), on a fictional company shaped like the hosted TIDY one (Nov 19 flight recorder: 225 calls an
hour, avg 2.1 s, slow ones holding the write lock 5.5 s): 165 bots on 10 computers, 16 full-login plans owned by three
people, four computers given one plan each and six given a person's plans, a few plans held at their limit, and turns
running on some plans. Most plans were read a while ago; the ones not next in line in a set are due but not wanted, as
on a live hub, so every poll asks about them.

    nice -n 10 .venv/bin/python bench/perf-claude-logins-poll.py

Env: RUNS (default 10, rounds of every computer polling once). Per round of steady polls it prints the median and p95
wall time per poll, the statements and SQL time per poll (the flight recorder's), how many polls took the write lock and
how long it was held, and a summary of what each computer was handed (by plan number, so two checkouts compare). Only
fictional data, in a temporary folder removed afterwards. Never point it at a real hub.
"""

import contextlib
import inspect
import dataclasses
import json
import os
import statistics
import sys
import tempfile
import time
from pathlib import Path

if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    import conftest  # noqa: F401  (the fictional registry and isolated benchmark settings)

from backend import claude_login as CL
from backend import login_holds
from backend import subscription_refresh as SR
from backend.store import H
from backend.tests.helpers import engine as X
from backend.tests.helpers.api import api_settings, assign, claim, headers, post, put, runner
from backend.tests.helpers.company import seed
from backend.tests.helpers.credentials import setup
from backend.tests.helpers.model_subscriptions import add, company, on

RUNS = int(os.environ.get("RUNS", 10))
RUNNERS = 10
CAPS = ["model_subscriptions_v1", "attempt_credentials_v1", "model_sign_out_v1", "model_connect_v1", "claude_login_v1"]
OWNERS = {"ana": 6, "ben": 5, "cara": 5}  # plans each person owns
TURNS = ("ops", "finance", "cpo")  # bots with a turn running, on computers 0-2


def full(email, n):
    now = int(time.time() * 1000)
    return json.dumps(
        {
            "claudeAiOauth": {
                "accessToken": f"sk-ant-oat01-access-{n}-" + "x" * 24,
                "refreshToken": f"sk-ant-ort01-refresh-{n}-" + "y" * 24,
                "expiresAt": now + 8 * 3600_000,
                "refreshTokenExpiresAt": now + 29 * 86_400_000,
                "scopes": ["user:inference", "user:profile"],
                "subscriptionType": "max",
            },
            "oauthAccount": {"emailAddress": email, "organizationUuid": "org-acme"},
        }
    )


def at(minutes):
    return H.shift(H.now(), seconds=minutes * 60)


def heartbeat(api, m, bots=()):
    post(
        api,
        "runners/heartbeat",
        {
            "version": "test",
            "platform": "linux",
            "capabilities": CAPS,
            "readiness": {
                "schema_version": 1,
                "runtimes": {"claude": {"installed": True, "authenticated": "missing"}},
                "bots": {b: {"runtime": "claude", "ready": True} for b in bots},
            },
        },
        token=m["token"],
    )


def build(api):
    """The fictional company: returns (computers, plans, live) where live[i] is computer i's running turns' plans."""
    store = api.app.state.store
    setup(api)
    company(api)
    computers = [runner(api, label=f"box{n}") for n in range(RUNNERS)]
    with store.transaction() as c:
        for n in range(165):
            slug = f"bot{n:03d}"
            X.bot(c, slug, computer=False)
            c.execute(
                "INSERT OR REPLACE INTO assignments VALUES(?,?,1,?,'human:ana')",
                (slug, computers[n % RUNNERS]["runner_id"], H.now()),
            )
    plans = []
    for owner, count in OWNERS.items():
        for _ in range(count):
            n = len(plans)
            sid = add(api, "claude_login", full(f"plan{n}@acme.example", n), name="")["id"]
            plans.append(sid)
            with store.transaction() as c:
                c.execute("UPDATE model_subscriptions SET owner_id=replace(owner_id,'ana',?) WHERE id=?", (owner, sid))
    cara = plans[OWNERS["ana"] + OWNERS["ben"] :]
    for n, m in enumerate(computers):
        bots = [TURNS[n]] if n < len(TURNS) else []
        for b in bots:
            on(api, b, "claude", "claude-sonnet-5-5")
            assign(api, m, b)
        heartbeat(api, m, bots)
        pick = (
            {"kind": "plan", "subscription_id": cara[n]}
            if n < 4
            else {"kind": "pool", "person": "ana" if n < 7 else "ben"}
        )
        with store.read() as c:
            placed = [r[0] for r in c.execute("SELECT bot FROM assignments WHERE runner_id=?", (m["runner_id"],))]
        for bot in placed:
            put(api, f"bots/{bot}/subscriptions", {"subscriptions": {"claude": pick}})
    # Every plan read 20 minutes ago and due again: the ones next in line get asked, the others stay due.
    with store.transaction() as c:
        for sid in plans:
            c.execute(
                "INSERT OR REPLACE INTO registry_metadata VALUES(?,?)",
                (SR.plan_key(sid), json.dumps({"schedule": {"read_at": at(-20), "next_at": at(-1)}})),
            )
        # A few plans held at their limit.
        for sid in (plans[1], plans[7], plans[13]):
            login_holds._save(
                c,
                {
                    "runner_id": "",
                    "profile": login_holds.subscription_login(sid),
                    "runtime": "claude",
                    "until": at(24 * 60),
                    "since": H.now(),
                    "error": "You've hit your limit",
                },
            )
    live = [[] for _ in computers]
    for n, b in enumerate(TURNS):
        post(api, f"chat/{b}", {"text": "Summarize the Acme account."})
        turn = claim(api, computers[n])
        if turn:
            post(api, f"attempts/{turn['id']}/started", {"thread_id": f"t-{n}"}, computers[n]["token"])
    with store.read() as c:
        for n, m in enumerate(computers):
            live[n] = [
                {"subscription": r[0], "revision": r[1]}
                for r in c.execute(
                    "SELECT s.subscription_id,s.revision FROM attempt_subscriptions s JOIN attempts a "
                    "ON a.id=s.attempt_id WHERE a.runner_id=? AND a.state IN ('leased','running')",
                    (m["runner_id"],),
                )
            ]
    return computers, plans, live


@contextlib.contextmanager
def watched(store, seen, holds):
    """Count every statement on the store's connections and time each write transaction's block."""
    read, transaction = store.read, store.transaction

    @contextlib.contextmanager
    def traced():
        with read() as c:
            c.set_trace_callback(lambda q: seen.append(q))
            try:
                yield c
            finally:
                c.set_trace_callback(None)

    @contextlib.contextmanager
    def timed(*a, **k):
        with transaction(*a, **k) as c:
            started = time.perf_counter()
            try:
                yield c
            finally:
                holds.append((time.perf_counter() - started) * 1000)

    store.read, store.transaction = traced, timed
    try:
        yield
    finally:
        store.read, store.transaction = read, transaction


def server_timing(response, name):
    for part in response.headers.get("server-timing", "").split(","):
        key, _, value = part.strip().partition(";dur=")
        if key == name:
            return float(value)
    return 0.0


def explain(api):
    with api.app.state.store.read() as c:
        where = (
            "WHERE subscription_id=? AND revision<? AND attempt_id IN "
            "(SELECT id FROM attempts WHERE runner_id=? AND state IN ('leased','running'))"
        )
        for label, sql, args in (
            (
                "UPDATE (every poll before; now only under the write lock)",
                "UPDATE attempt_subscriptions SET revision=min(?,(SELECT revision FROM model_subscriptions WHERE id=?)) "
                + where,
                (1, "s", "s", 1, "r"),
            ),
            (
                "SELECT (the read-only poll's check)",
                f"SELECT 1 FROM attempt_subscriptions {where} LIMIT 1",
                ("s", 1, "r"),
            ),
        ):
            print("EXPLAIN QUERY PLAN", label)
            for row in c.execute("EXPLAIN QUERY PLAN " + sql, args):
                print("   ", row[3])


def main():
    from fastapi.testclient import TestClient

    from backend.app import create_app

    with tempfile.TemporaryDirectory(prefix=".tmp-claude-poll-", dir=Path(__file__).resolve().parents[1]) as directory:
        settings = dataclasses.replace(api_settings(Path(directory)), flight_recorder=True)
        app = create_app(settings)
        with TestClient(app) as api:
            with app.state.store.transaction() as c:
                seed(c)
            t = time.perf_counter()
            computers, plans, live = build(api)
            print(f"seeded in {time.perf_counter() - t:.1f}s; running turns: {sum(len(x) for x in live)}")
            explain(api)
            number = {sid: n for n, sid in enumerate(plans)}

            def poll(n):
                return api.post(
                    "/api/v2/runner-claude-logins",
                    json={"live": live[n]},
                    headers=headers(computers[n]["token"]),
                )

            # The first round asks the usage reads that are due (writes); the rounds after it are the steady state.
            store = app.state.store
            for label, rounds in (("first round (after a restart: reads to ask)", 1), ("steady state", RUNS)):
                walls, sqls, counts, holds, handed = [], [], [], [], {}
                for _ in range(rounds):
                    for n in range(RUNNERS):
                        seen, held = [], []
                        with watched(store, seen, held):
                            started = time.perf_counter()
                            r = poll(n)
                            walls.append((time.perf_counter() - started) * 1000)
                        assert r.status_code == 200, r.text[:300]
                        sqls.append(server_timing(r, "sql"))
                        counts.append(len(seen))
                        holds.extend(held)
                        body = r.json()
                        handed[n] = sorted(
                            [("token", number[x["subscription"]]) for x in body["tokens"]]
                            + [("refresh", number[x["subscription"]]) for x in body["refreshes"]]
                            + [("usage", number[x["subscription"]]) for x in body["usage"]]
                        )
                walls.sort()
                print(f"--- {label}: {len(walls)} polls")
                print(
                    f"wall ms: median {statistics.median(walls):.1f}  p95 {walls[int(len(walls) * 0.95) - 1]:.1f}  "
                    f"max {walls[-1]:.1f}  total {sum(walls):.0f}"
                )
                print(f"SQL ms per poll: median {statistics.median(sqls):.1f}  total {sum(sqls):.0f}")
                print(f"statements per poll: median {statistics.median(counts):.0f}  max {max(counts)}")
                print(
                    f"write lock: {len(holds)} of {len(walls)} polls took it; held {sum(holds):.0f} ms in all, "
                    f"max {max(holds, default=0):.1f} ms"
                )
                # Which computer of a person's set reads a plan goes by runner id (random per run): compare the plans.
                print("handed (kind, plan):", sorted(x for v in handed.values() for x in v))
            # The route's own work for a steady poll, outside HTTP (whose auth and middleware cost the same either way),
            # and where its SQL time goes (the flight recorder's per-request costs).
            from backend import flight

            def handler(n, body):
                if "write" in inspect.signature(CL.poll).parameters:
                    with store.read_transaction() as c:
                        return CL.poll(c, app.state.vault, computers[n]["runner_id"], body, write=False)
                with store.transaction() as c:
                    return CL.poll(c, app.state.vault, computers[n]["runner_id"], body)

            for n in range(RUNNERS):
                body = CL.Poll.model_validate({"live": live[n]})
                handler(n, body)
                times = []
                for _ in range(20):
                    started = time.perf_counter()
                    handler(n, body)
                    times.append((time.perf_counter() - started) * 1000)
                cost, token = flight.request_started()
                try:
                    handler(n, body)
                finally:
                    flight.request_finished(token)
                print(
                    f"--- computer {n}: handler median {statistics.median(times):.2f} ms, {cost[2]} statements, "
                    f"{cost[0]:.2f} ms SQL"
                )
                if n in (0, 4):
                    for sql, (k, ms) in sorted(cost[3].items(), key=lambda kv: -kv[1][1])[:6]:
                        print(f"{ms:7.2f} ms {k:4d}x  {' '.join(sql.split())[:150]}")


if __name__ == "__main__":
    main()
