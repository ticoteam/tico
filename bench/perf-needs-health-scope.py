"""Measures Needs you and Health for each kind of reader on a fictional company shaped like the hosted TIDY one (Nov 19:
1,155 open pipeline tasks in 19 pipelines, the biggest 491, 375 and 225; 77 of them private, 38 with a reader list;
722 finished turns in the last day across 63 bots, their results about 2.4 KB each).

    nice -n 10 .venv/bin/python bench/perf-needs-health-scope.py

The readers: Ana, the company's person; Ben, an Admin who owns a 35-task pipeline; Cara, who owns a 14-task pipeline;
Eve, who asked for private tasks and is on reader lists; Fay, who reads private tasks only through the Ops group.

Per route and reader it prints the statements one call takes and the median wall time of RUNS calls. Env: RUNS (7),
DB=<file> (keep the seeded database and reuse it: seed once, then run two checkouts on the same rows), ANSWERS=<file>
(each answer as JSON, the clock's own fields blanked, to compare two checkouts).

Only fictional data, in a temporary folder removed afterwards (unless DB is given). Never point it at a real hub.
"""

import importlib.util
import json
import os
import random
import shutil
import statistics
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if __name__ == "__main__":
    sys.path.insert(0, str(ROOT))
    import conftest  # noqa: F401  (the fictional registry and isolated benchmark settings)

_spec = importlib.util.spec_from_file_location("perf_hub_overload", ROOT / "bench" / "perf-hub-overload.py")
assert _spec and _spec.loader
overload = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(overload)
_spec = importlib.util.spec_from_file_location("perf_status_health", ROOT / "bench" / "perf-status-health.py")
assert _spec and _spec.loader
status_health = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(status_health)

from backend.store import H
from backend.tests.helpers import engine as X
from backend.tests.helpers.api import headers

RUNS = int(os.environ.get("RUNS", 7))
TOKENS = ("ana-test", "ben-test", "cara-test", "eve-test", "fay-test")
PATHS = ("/api/v2/needs-you", "/api/v2/health")
SIZES = (491, 375, 225, 35, 14, 6, 4, 3, 1, 1)  # open tasks per pipeline; nine more pipelines have none
BOTS = [f"bot{n:03d}" for n in range(63)]
STAGES = (
    {"name": "Build", "owner": "assignee", "requirements": [{"kind": "owner_done"}]},
    {"name": "Review", "owner": "assignee", "requirements": [{"kind": "ok", "person": X.ANA, "line": "OK the build"}]},
)


def seed(api):
    from backend import access as Access
    from backend.views import roster

    store, rnd = api.app.state.store, random.Random(19)
    with store.transaction() as c:
        for slug, name in (("eve", "Eve"), ("fay", "Fay")):
            c.execute("INSERT OR IGNORE INTO humans(id,name,email) VALUES(?,?,?)", (slug, name, f"{slug}@acme.example"))
        whole = roster(c)
        people = [*whole["people"], {"id": "eve", "name": "Eve"}, {"id": "fay", "name": "Fay", "team": "ops"}]
        Access.save_roster(c, {**whole, "people": people, "org_groups": {"ops": {"name": "Ops"}}})
        for slug in BOTS:
            X.bot(c, slug, computer=False)
        for n in range(19):
            X.pipeline(c, f"p{n:02d}", f"Pipeline {n}", STAGES, manager=f"bot:{BOTS[n]}" if n < 3 else None)
        c.execute("UPDATE pipelines SET owner_id='human:ben' WHERE id='p03'")
        c.execute("UPDATE pipelines SET owner_id='human:cara' WHERE id='p04'")
    k = 0
    for n, size in enumerate(SIZES):
        for start in range(0, size, 200):
            with store.transaction() as c:
                for _ in range(start, min(size, start + 200)):
                    k += 1
                    requester = "human:eve" if k % 30 == 0 else X.ANA
                    row = X.new_task(c, requester, f"Task {k}", f"p{n:02d}", assignee=f"bot:{BOTS[k % len(BOTS)]}")
                    if k % 4 == 0:
                        X.ok_ask(c, row["id"], row["owner_id"], X.ANA, f"OK {k}")
                    if k % 9 == 0:  # an OK naming nobody: the task falls to its ladder's person
                        X.ok_ask(c, row["id"], row["owner_id"], X.ANA, f"OK anyone {k}")
                        c.execute("UPDATE requirements SET person_id=NULL WHERE line=?", (f"OK anyone {k}",))
                    if k % 15 == 0:
                        readers = (["human:eve"], ["group:ops"], ["human:cara"], None)[(k // 15) % 4]
                        c.execute(
                            "UPDATE tasks SET private=1, readers=? WHERE id=?",
                            (H._dump(readers) if readers else None, row["id"]),
                        )
                    if k % 23 == 0:
                        c.execute("UPDATE visits SET level='person' WHERE id=?", (row["visit"]["id"],))
    # Finished turns: 722 in the last day, more before; a few ran on a fallback, or on the Gateway for a sign-in.
    with store.transaction() as c:
        for n in range(4000):
            hours = n * 24 / 722
            at = H.shift(H.now(), hours=-hours)
            result = {"final": "x" * rnd.randint(500, 4400)}
            if rnd.random() < 0.1:
                result["fallback"] = "codex"
            if rnd.random() < 0.05:
                result["waiting_for_sign_in"] = "claude"
            c.execute(
                "INSERT INTO attempts(id,bot_id,state,created_at,started_at,finished_at,result,usage_model) "
                "VALUES(?,?,?,?,?,?,?,?)",
                (
                    H.new_id(),
                    BOTS[n % len(BOTS)],
                    ("completed", "completed", "failed", "interrupted")[n % 4],
                    at,
                    at,
                    at,
                    json.dumps(result),
                    rnd.choice(["claude-opus", "gpt-6", None]),
                ),
            )
    from backend import health

    health.record_never_stuck(store)


def measure(api, path, token):
    store, seen, times = api.app.state.store, [], []
    first = api.get(path, headers=headers(token))
    assert first.status_code == 200, (path, first.status_code, first.text[:300])
    with overload.counting(store, seen):
        api.get(path, headers=headers(token))
    for _ in range(RUNS):
        t = time.perf_counter()
        api.get(path, headers=headers(token))
        times.append((time.perf_counter() - t) * 1000)
    return seen, statistics.median(times), first.json()


def report(api):
    with api.app.state.store.read() as c:
        print(
            "open pipeline tasks",
            c.execute("SELECT count(*) FROM tasks WHERE pipeline_id IS NOT NULL AND status='open'").fetchone()[0],
            "private",
            c.execute("SELECT count(*) FROM tasks WHERE status='open' AND private IS NOT 0").fetchone()[0],
            "turns",
            c.execute("SELECT count(*) FROM attempts").fetchone()[0],
        )
    answers = {}
    for path in PATHS:
        for token in TOKENS:
            seen, ms, answer = measure(api, path, token)
            items = answer.get("items") if isinstance(answer, dict) else None
            size = len(items) if isinstance(items, list) else len(answer.get("checks") or [])
            print(f"{path:22s} {token:9s} {len(seen):5d} statements {ms:8.1f} ms  {size:4d} items")
            answers[f"{path} {token}"] = status_health.blank_clock(answer)
    if os.environ.get("ANSWERS"):
        Path(os.environ["ANSWERS"]).write_text(json.dumps(answers, sort_keys=True, default=str))


def main():
    from backend.tests.helpers.api_app import api_client

    keep = os.environ.get("DB")
    with tempfile.TemporaryDirectory(prefix=".tmp-needs-health-", dir=ROOT) as directory:
        folder = Path(directory)
        reuse = keep and Path(keep).exists()
        install = overload.reopened(folder, keep) if reuse else api_client(folder, None)
        api = next(install)
        try:
            if not reuse:
                t = time.perf_counter()
                seed(api)
                print(f"seeded in {time.perf_counter() - t:.1f}s")
                if keep:
                    with api.app.state.store.read() as c:
                        c.execute("VACUUM INTO ?", (keep,))
            report(api)
        finally:
            install.close()
            shutil.rmtree(folder, ignore_errors=True)


if __name__ == "__main__":
    main()
