"""Measures a search or a filter on the Tasks page (perf I16): per search and filter, the requests made and the bytes
received until the list is final, before (the page read every remaining page of the view and matched in the browser)
and after (the server answers one bounded page of matches), on a fictional pipeline of 1,500 open tasks and an archive
of 5,000 done ones.

    nice -n 19 python3 bench/perf-task-search.py

"Before" is the reads the old page made once a search or a filter was set: every page of the view after the first,
500 rows each (the first was already on screen). "After" is the first page of matches (100 rows at most), and the
pages More reads to reach every match. Median wall time is of RUNS calls of the first answer.
"""

import os
import random
import statistics
import sys
import time
import urllib.parse
from pathlib import Path

if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    import conftest  # noqa: F401  (the fictional registry and isolated benchmark settings)

from backend.store import H
from backend.tests.helpers.api import headers

OPEN, DONE = int(os.environ.get("OPEN", 1500)), int(os.environ.get("DONE", 5000))
RUNS = 7
WORDS = "invoice latency checkout onboarding pricing export billing search mobile report".split()
OWNERS = ["human:ana", "human:ben", "human:cara", "bot:ops"]


def seed(api):
    stage = {"owner": "assignee", "requirements": [{"kind": "owner_done"}]}
    record = {
        "name": "Product",
        "goal": "Specs that ship",
        "stages": [{"id": "spec", "name": "Spec", **stage}, {"id": "build", "name": "Build", **stage}],
    }
    r = api.post("/api/v2/pipelines/product", json=record, headers=headers("ana-test"))
    assert r.status_code == 200, r.text
    rnd = random.Random(16)
    store = api.app.state.store
    made, now = [], H.now()
    for start in range(0, OPEN + DONE, 250):
        with store.transaction() as c:
            for n in range(start, min(start + 250, OPEN + DONE)):
                title = f"{rnd.choice(WORDS).title()} {rnd.choice(WORDS)} task {n}" + (" audit" if n % 166 == 0 else "")
                owner = OWNERS[n % len(OWNERS)]
                t = H.task_create(
                    c,
                    "human:ana",
                    title,
                    "A body the list never sends.",
                    owner,
                    pipeline="product",
                    labels=[rnd.choice(WORDS)],
                    deduplicate=False,
                    lint=False,
                    own_room=False,
                    due=H.shift(now, days=n % 21 - 10) if n % 7 == 0 else None,
                )
                made.append(t["id"])
    from backend import task_engine

    done = made[OPEN:]
    for start in range(0, len(done), 250):
        with store.transaction() as c:
            for tid in done[start : start + 250]:
                task_engine.move(c, tid, "done", "human:ana", why="Finished.")


def get(api, query):
    t = time.perf_counter()
    r = api.get("/api/v2/tasks?brief=1&" + query, headers=headers("ana-test"))
    ms = (time.perf_counter() - t) * 1000
    assert r.status_code == 200, r.text
    return r.json(), len(r.content), ms


def before(api, view, first_limit):
    """The old page: the view's remaining pages, 500 rows each, from where its first page ended."""
    n = size = 0
    offset = first_limit
    while True:
        page, b, _ = get(api, f"{view}&limit=500&offset={offset}")
        n, size = n + 1, size + b
        if page["next_offset"] is None:
            return n, size
        offset = page["next_offset"]


def after(api, view, narrow):
    """The first page of matches (its rows, bytes and median ms), and every page More reads after it, to the end."""
    first, first_size, _ = get(api, f"{view}&limit=100&{narrow}")
    ms = statistics.median(get(api, f"{view}&limit=100&{narrow}")[2] for _ in range(RUNS))
    n, size, rows, page = 1, first_size, len(first["tasks"]), first
    while page["next_after"]:
        page, b, _ = get(api, f"{view}&limit=100&{narrow}&after={page['next_after']}")
        n, size, rows = n + 1, size + b, rows + len(page["tasks"])
    return (len(first["tasks"]), first_size, ms), (n, rows, size)


def test_task_search(api):
    t = time.perf_counter()
    seed(api)
    print(f"\nseeded {OPEN} open and {DONE} done tasks in {time.perf_counter() - t:.1f}s")
    pipeline = "pipeline=product&status=open&snoozed=hide&sort=queue"
    archive = "pipeline=product&status=done&snoozed=hide&sort=finished"
    cases = [
        ("pipeline", pipeline, 500, {"search": "zzqx"}),
        ("pipeline", pipeline, 500, {"search": "invoice"}),
        ("pipeline", pipeline, 500, {"search": "latency checkout"}),
        ("pipeline", pipeline, 500, {"owners": "human:ben"}),
        ("pipeline", pipeline, 500, {"owners": "human:ben,human:cara", "search": "report"}),
        ("pipeline", pipeline, 500, {"stages": "spec"}),
        ("pipeline", pipeline, 500, {"labels": "pricing"}),
        ("pipeline", pipeline, 500, {"turn": "human:ben"}),
        ("pipeline", pipeline, 500, {"due": "any"}),
        ("pipeline", pipeline, 500, {"late": "true"}),
        ("archive", archive, 200, {"search": "zzqx"}),
        ("archive", archive, 200, {"search": "audit"}),
        ("archive", archive, 200, {"owners": "bot:ops"}),
    ]
    print(
        f"{'view':9s} {'search or filter':42s} | {'before: req':>11s} {'KB':>8s} | {'after: req':>10s} {'rows':>5s} "
        f"{'KB':>7s} {'ms':>6s} | {'to the end: req':>15s} {'rows':>5s} {'KB':>8s}"
    )
    for name, view, first_limit, narrow in cases:
        q = urllib.parse.urlencode(narrow)
        n_before, b_before = before(api, view, first_limit)
        (rows1, b1, ms), (n_all, rows_all, b_all) = after(api, view, q)
        print(
            f"{name:9s} {urllib.parse.unquote(q):42s} | {n_before:11d} {b_before / 1024:8.1f} | {1:10d} {rows1:5d} {b1 / 1024:7.1f} "
            f"{ms:6.1f} | {n_all:15d} {rows_all:5d} {b_all / 1024:8.1f}"
        )


if __name__ == "__main__":
    import tempfile

    from backend.tests.helpers.api_app import api_client

    with tempfile.TemporaryDirectory(prefix=".tmp-search-", dir=Path(__file__).resolve().parents[1]) as directory:
        install = api_client(Path(directory), None)
        try:
            test_task_search(next(install))
        finally:
            install.close()
