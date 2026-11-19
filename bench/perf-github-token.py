"""Measures POST /api/v2/github/token, the runners' GitHub token request (TIDY Nov 19 flight recorder: 556 calls an hour
from ~10 computers, avg 578 ms, p50 26 ms but slow samples a median 5.2 s with 4.2 s waiting on the write lock and 33
statements; registry_metadata read 13-15 times a call; p50 376 ms in the two minutes after each restart), on a
fictional company at that size: 165 bots on 10 computers, 40 shared repositories, a third of the bots on chosen
repositories (some read, some write, a few that run workflows), a few on All ticked repos. GitHub is faked.

    nice -n 10 .venv/bin/python bench/perf-github-token.py

Scenarios, each over every bot once:
  mint      the first token after the App is connected: every scope minted on (fake) GitHub
  hit       the git credential helper's ask a minute later: every token from the cache
  restart   the hub restarted (every in-memory cache, the app JWT and the audit marks gone): what each ask costs then
  hit+load  `hit` again while another thread keeps taking the write lock (LOAD_MS held, a little apart), as a busy
            hub does: a request that takes the write lock waits for it
Per scenario: median and p90 wall ms, SQL ms, statements, write transactions and write-lock wait per request, and the
GitHub calls made. Env: RUNS (passes per scenario, default 2), LOAD_MS (default 40), STATEMENTS=1 (each scenario's
statements by count, for one request). Only fictional data, in a temporary folder removed afterwards.
"""

import collections
import contextlib
import os
import shutil
import statistics
import sys
import tempfile
import threading
import time
import uuid
from pathlib import Path

if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    import conftest  # noqa: F401  (the fictional registry and isolated benchmark settings)

import httpx
from fastapi.testclient import TestClient

from backend import bot_settings as BS
from backend import github_app as G
from backend import hubdb as H
from backend import repositories as R
from backend.app import create_app
from backend.auth import Identity
from backend.config import Settings
from backend.store import encode
from backend.tests.helpers.github_app import PUBLIC, FakeGitHub, connect

BOTS = [f"bot{n:03d}" for n in range(165)]
RUNNERS = [f"r{n}" for n in range(10)]
REPOS = [f"Acme/shared-{n:02d}" for n in range(40)]
RUNS = int(os.environ.get("RUNS", 2))
LOAD_MS = float(os.environ.get("LOAD_MS", 40))


def seed(api):
    store = api.app.state.store
    with store.transaction() as c:
        c.execute(
            "INSERT OR REPLACE INTO registry_metadata VALUES('onboarding',?)",
            (encode({"completed": "2026-01-01T00:00:00Z"}),),
        )
        H.sync_registry(
            c,
            {slug: {"name": slug, "runtime": "fake", "status": "active"} for slug in BOTS},
            {"people": [{"id": "ana", "email": "ana@acme.example", "team": "leadership"}]},
        )
        for rid in RUNNERS:
            c.execute(
                "INSERT OR IGNORE INTO runners(id,label,owner_id,token_hash,created_at) VALUES(?,?,'ana',?,?)",
                (rid, "box" + rid, "h" + rid, H.now()),
            )
        for repo in REPOS:
            c.execute(
                "INSERT INTO repositories(id,full_name,enabled,bot_repo,reachable,updated_at) VALUES(?,?,1,0,1,?)",
                (uuid.uuid4().hex, repo, H.now()),
            )
        flagged = {}
        for n, slug in enumerate(BOTS):
            c.execute("INSERT INTO bot_config(bot,team,owner_id,repo) VALUES(?,'t','ana',?)", (slug, "emp-" + slug))
            c.execute(
                "INSERT OR REPLACE INTO assignments VALUES(?,?,1,?,'human:ana')", (slug, RUNNERS[n % 10], H.now())
            )
            config = {"name": slug, "runtime": "fake", "status": "active"}
            if n % 3 == 0:  # chosen repositories: two to five shared ones, the first written, the rest read
                config["repo_access_mode"] = "chosen"
                for k in range(2 + n % 4):
                    name = REPOS[(n + 7 * k) % len(REPOS)]
                    c.execute(
                        "INSERT OR REPLACE INTO bot_repo_access(bot,full_name,access) VALUES(?,?,?)",
                        (slug, name, "write" if k == 0 else "read"),
                    )
                    if k == 0 and n % 9 == 0:
                        flagged.setdefault(slug, []).append(name)
            elif n % 20 == 1:
                config.update({"repo_access_mode": "all", "repo_all_access": "read"})
            BS.write(c, slug, config)
        R.save_metadata(c, R.STORED["run_workflows"], flagged)


class Meter:
    """Each request's flight-recorder cost (SQL ms, write-lock wait, statements) and its write transactions."""

    def __init__(self, store):
        self.store, self.last, self.writes = store, None, 0
        recorder = store.recorder
        request, self.transaction = recorder.request, store.transaction

        def seen(route, caller, status, ms, nbytes=0, actor="", cost=None, client=""):
            if route.endswith("/github/token"):
                self.last = (ms, cost[0], cost[1], cost[2], dict(cost[3]) if len(cost) > 3 else {})
            return request(route, caller, status, ms, nbytes, actor, cost, client)

        @contextlib.contextmanager
        def counted(*a, **kw):
            if threading.current_thread() is not self.loader:
                self.writes += 1
            with self.transaction(*a, **kw) as c:
                yield c

        recorder.request = seen
        store.transaction = counted
        self.loader = None


def restart(api):
    """What a new process starts with: no token cache, no JWT, no audit marks, nothing learned live."""
    service = api.app.state.github_app
    with service.lock:
        service.cache.clear()
        service.live.clear()
        service.audited_tokens.clear()


def scenario(api, gh, meter, name, purpose, before=None, load=False):
    rows, statements = [], collections.Counter()
    calls = len(gh.calls)
    stop = threading.Event()
    if load:

        def hold():
            while not stop.is_set():
                with meter.transaction() as c:
                    c.execute("UPDATE registry_metadata SET value=value WHERE key='onboarding'")
                    time.sleep(LOAD_MS / 1000)
                time.sleep(0.005)

        meter.loader = threading.Thread(target=hold, daemon=True)
        meter.loader.start()
    try:
        for _ in range(RUNS):
            if before:
                before()
            for n, bot in enumerate(BOTS):
                writes = meter.writes
                t = time.perf_counter()
                r = api.post(
                    "/api/v2/github/token",
                    json={"bot": bot, **({"purpose": purpose} if purpose else {})},
                    headers={"Authorization": f"Bearer runner-{RUNNERS[n % 10]}"},
                )
                wall = (time.perf_counter() - t) * 1000
                assert r.status_code == 200, r.text[:300]
                ms, sql_ms, wait_ms, queries, shapes = meter.last
                rows.append((wall, sql_ms, wait_ms, queries, meter.writes - writes))
                if not statements:
                    statements.update({k: v[0] for k, v in shapes.items()})
    finally:
        stop.set()
        if meter.loader:
            meter.loader.join()
            meter.loader = None
    made = gh.calls[calls:]
    mints = sum(1 for c in made if c[1].endswith("/access_tokens"))

    def col(i, q=0.5):
        values = sorted(r[i] for r in rows)
        return values[min(len(values) - 1, int(q * len(values)))]

    print(
        f"{name:9s} n={len(rows):4d}  wall p50 {col(0):7.1f} p90 {col(0, 0.9):7.1f} mean {statistics.mean(r[0] for r in rows):7.1f} ms"
        f" | SQL p50 {col(1):6.1f} ms | stmts p50 {col(3):3d} | write txns/req mean {statistics.mean(r[4] for r in rows):.2f}"
        f" max {max(r[4] for r in rows)} | lock wait mean {statistics.mean(r[2] for r in rows):6.1f} ms"
        f" | GitHub mints {mints} other calls {len(made) - mints}"
    )
    if os.environ.get("STATEMENTS"):
        for sql, n in statements.most_common(12):
            print(f"      {n:3d}x {sql[:140]}")
    return rows


def main():
    gh = FakeGitHub()
    gh.permissions = {
        "contents": "write",
        "pull_requests": "write",
        "issues": "write",
        "metadata": "read",
        "actions": "write",
        "checks": "read",
        "statuses": "read",
        "deployments": "read",
    }
    G.TRANSPORT = httpx.MockTransport(gh)
    folder = Path(tempfile.mkdtemp(prefix=".tmp-ghtoken-", dir=Path(__file__).resolve().parents[1]))
    ids = {"owner-test": Identity("human:ana", "owner", "ana@acme.example")}
    ids.update({f"runner-{rid}": Identity(f"runner:{rid}", "runner", runner_id=rid) for rid in RUNNERS})
    app = create_app(
        Settings(
            db_path=folder / "hub.db",
            public_url=PUBLIC,
            company_name="Acme",
            github_owner="Acme",
            test_identities=ids,
            flight_recorder=True,
        )
    )
    try:
        with TestClient(app, follow_redirects=False) as api:
            api.app_state = app.state
            seed(api)
            connect(api)
            meter = Meter(app.state.store)
            print(f"{len(BOTS)} bots, {len(RUNNERS)} computers, {len(REPOS)} shared repositories")
            scenario(api, gh, meter, "mint", "turn", before=restart_all(api))
            scenario(api, gh, meter, "hit", "git")
            scenario(api, gh, meter, "restart", "git", before=lambda: restart(api))
            scenario(api, gh, meter, "hit+load", "git", load=True)
            scenario(api, gh, meter, "rst+load", "git", before=lambda: restart(api), load=True)
            explain(app.state.store)
    finally:
        shutil.rmtree(folder, ignore_errors=True)


KEYS = (
    "repositories-confirmed-missing",
    "repository-creation-grants",
    R.PRODUCT_GRANTS,
    *(key for _, key, *_ in R.FLAGS),
    *R.ALL_STORED.values(),
    R.SELECTION,
    R.MADE,
    R.CHOSEN,
    R.TAKEN,
    R.CREATED_ON_GITHUB,
)


def explain(store):
    """The plan and time of each statement this change adds or replaces, on the seeded database."""
    one = "SELECT value FROM registry_metadata WHERE key=?"
    many = "SELECT value,key FROM registry_metadata WHERE key IN (" + ",".join("?" * len(KEYS)) + ")"
    with store.read() as c:
        has = c.execute("SELECT 1 FROM sqlite_master WHERE name='github_installation_tokens'").fetchone()
        cases = [
            (f"old: {len(KEYS)} x " + one, lambda: [c.execute(one, (k,)).fetchall() for k in KEYS], one, KEYS[:1]),
            ("new: " + many, lambda: c.execute(many, KEYS).fetchall(), many, KEYS),
        ]
        if has:
            kept = "SELECT expires_at,nonce,ciphertext FROM github_installation_tokens WHERE scope=?"
            scope = c.execute("SELECT scope FROM github_installation_tokens LIMIT 1").fetchone()
            args = (scope[0] if scope else "x",)
            cases.append(("new: " + kept, lambda: c.execute(kept, args).fetchall(), kept, args))
            prune = "SELECT count(*) FROM github_installation_tokens WHERE expires_at<?"  # the prune's scan
            cases.append(("new: " + prune, lambda: c.execute(prune, ("0",)).fetchall(), prune, ("0",)))
            print("kept tokens:", c.execute("SELECT count(*) FROM github_installation_tokens").fetchone()[0], "rows")
        for label, run, sql, args in cases:
            plan = " / ".join(r[3] for r in c.execute("EXPLAIN QUERY PLAN " + sql, args))
            t = time.perf_counter()
            for _ in range(2000):
                run()
            print(f"{label[:90]:90s} {(time.perf_counter() - t) / 2000 * 1000:.4f} ms  [{plan}]")


def restart_all(api):
    """A restart that finds no token kept anywhere: what the first tokens after connecting the App cost."""

    def run():
        restart(api)
        forget = getattr(api.app.state.github_app, "forget_tokens", None)
        if forget:
            forget()

    return run


if __name__ == "__main__":
    main()
