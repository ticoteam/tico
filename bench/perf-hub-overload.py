"""Measures the reads that loaded the hosted TIDY company's server most (Nov 18 flight recorder: GET /api/v2/bots
803 an hour at 400 KB each, GET /api/v2/threads/rail p50 21 s, GET /api/v2/health p50 17 s) on a fictional company
at that size: 165 bots on 4 computers, OPEN open tasks (most owned by bots, each with a few OK asks), CLOSED finished
tasks, a few hundred threads with posts, and a message history.

    nice -n 10 .venv/bin/python bench/perf-hub-overload.py

Env: OPEN (default 1500), CLOSED (16500), THREADS (400), RUNS (5), PROFILE=<path> (cProfile one route),
STATEMENTS=<path> (the statement shapes one call runs), DB=<file> (keep the seeded database there and reuse it),
PATHS=<path,...> (the routes measured), ANSWERS=<file> (each route's answer as JSON, to compare two checkouts' on one DB).

Per route it prints the statements one call takes, the median wall time of RUNS calls and the answer's size. Only
fictional data, in a temporary folder removed afterwards (unless DB is given). Never point it at a real hub.
"""

import collections
import contextlib
import json
import os
import random
import re
import shutil
import sqlite3
import statistics
import sys
import time
from pathlib import Path

if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    import conftest  # noqa: F401  (the fictional registry and isolated benchmark settings)

from backend import threads
from backend.store import H, Problem
from backend.tests.helpers import engine as X
from backend.tests.helpers.api import headers

BOTS = [f"bot{n:03d}" for n in range(165)]
OPEN = int(os.environ.get("OPEN", 1500))
CLOSED = int(os.environ.get("CLOSED", 16500))
THREADS = int(os.environ.get("THREADS", 400))
RUNS = int(os.environ.get("RUNS", 5))
STAGES = (
    {"name": "Build", "owner": "assignee", "requirements": [{"kind": "owner_done"}]},
    {"name": "Review", "owner": "assignee", "requirements": [{"kind": "ok", "person": X.ANA, "line": "OK the build"}]},
)
PATHS = tuple(os.environ.get("PATHS", "/api/v2/bots,/api/v2/threads/rail,/api/v2/health,/api/v2/me").split(","))


def seed(api):
    store = api.app.state.store
    rnd = random.Random(11)
    with store.transaction() as c:
        for n, slug in enumerate(BOTS):
            X.bot(c, slug, computer=False)
            runner = f"r{n % 4}"
            c.execute(
                "INSERT OR IGNORE INTO runners(id,label,owner_id,token_hash,created_at) VALUES(?,?,'ana',?,?)",
                (runner, "box" + runner, "h" + runner, H.now()),
            )
            c.execute("INSERT OR REPLACE INTO assignments VALUES(?,?,1,?,'human:ana')", (slug, runner, H.now()))
        X.pipeline(c, "eng", "Engineering", STAGES, manager="bot:" + BOTS[0])
    for start in range(0, OPEN, 200):
        with store.transaction() as c:
            for k in range(start, min(OPEN, start + 200)):
                owner = "bot:" + BOTS[k % len(BOTS)] if k % 8 else rnd.choice(["human:ana", "human:ben"])
                row = X.new_task(c, X.ANA, f"Open task {k}", "eng", assignee=owner)
                if k % 4 == 0:
                    X.ok_ask(c, row["id"], row["owner_id"], X.ANA, f"OK {k}")
    # Finished tasks: copies of the open ones, closed, each with its own room, so every scan of tasks and rooms pays.
    with store.transaction() as c:
        cols = [r[1] for r in c.execute("PRAGMA table_info(tasks)")]
        ccols = [r[1] for r in c.execute("PRAGMA table_info(conversations)")]
        src = [r[0] for r in c.execute("SELECT id FROM tasks WHERE status='open' ORDER BY rowid")]
        made = 0
        while made < CLOSED:
            for tid in src:
                if made >= CLOSED:
                    break
                new, room = H.new_id(), H.new_id()
                sel = ",".join(
                    "?"
                    if col == "id"
                    else ("NULL" if col in ("number", "stage_id", "pipeline_id", "stage_entered_at") else col)
                    for col in cols
                )
                c.execute(f"INSERT INTO tasks({','.join(cols)}) SELECT {sel} FROM tasks WHERE id=?", (new, tid))
                c.execute("INSERT OR REPLACE INTO task_move_guards (id, open, task_id) VALUES (1, 1, ?)", (new,))
                c.execute(
                    "UPDATE tasks SET status='done', pipeline_id=(SELECT pipeline_id FROM tasks WHERE id=?) WHERE id=?",
                    (tid, new),
                )
                c.execute("UPDATE task_move_guards SET open=0, task_id=NULL WHERE id=1")
                csel = ",".join("?" if col == "id" else ("?" if col == "task_id" else col) for col in ccols)
                with contextlib.suppress(sqlite3.IntegrityError):
                    c.execute(
                        f"INSERT INTO conversations({','.join(ccols)}) SELECT {csel} FROM conversations "
                        "WHERE task_id=? AND parent_id IS NULL LIMIT 1",
                        (room, new, tid),
                    )
                made += 1
    people = ["human:ana", "human:ben", "human:cara"]
    rooms = []
    with store.transaction() as c:
        for n in range(THREADS):
            starter = rnd.choice(people + ["bot:" + b for b in BOTS[:30]])
            text = f"Thread {n} kickoff" + (" @ana please look" if n % 3 == 0 else "")
            out = threads.post(c, starter, None, text, start={"title": f"Topic {n}", "audience": "company"})
            rooms.append(out.get("thread_id") or out.get("conversation_id"))
        for n in range(60):
            out = threads.post(c, "human:ana", None, f"Hi #{n}", start={"dm": ["bot:" + BOTS[n]], "title": ""})
            rooms.append(out.get("thread_id") or out.get("conversation_id"))
    for chunk in range(0, len(rooms), 50):
        with store.transaction() as c:
            for tid in rooms[chunk : chunk + 50]:
                for k in range(5):
                    who = rnd.choice(people + ["bot:" + b for b in BOTS])
                    with contextlib.suppress(Problem):
                        threads.post(c, who, tid, f"Note {k}" + (" @ana" if rnd.random() < 0.15 else ""))


@contextlib.contextmanager
def counting(store, seen):
    originals = {name: getattr(store, name) for name in ("read", "read_transaction")}

    def traced(opened):
        @contextlib.contextmanager
        def borrowed():
            with opened() as c:
                c.set_trace_callback(seen.append)
                try:
                    yield c
                finally:
                    c.set_trace_callback(None)

        return borrowed

    try:
        for name, opened in originals.items():
            setattr(store, name, traced(opened))
        yield
    finally:
        for name, opened in originals.items():
            setattr(store, name, opened)


def measure(api, path, token="ana-test"):
    store, times, seen = api.app.state.store, [], []
    r = api.get(path, headers=headers(token))
    assert r.status_code == 200, r.text[:300]
    with counting(store, seen):
        api.get(path, headers=headers(token))
    for _ in range(RUNS):
        t = time.perf_counter()
        api.get(path, headers=headers(token))
        times.append((time.perf_counter() - t) * 1000)
    return len(seen), statistics.median(times), len(r.content)


def report(api):
    answers = {}
    with api.app.state.store.read() as c:
        counts = {
            t: c.execute(f"SELECT count(*) FROM {t}").fetchone()[0]
            for t in ("bots", "tasks", "conversations", "messages", "visits", "requirements")
        }
    print(counts)
    for path in PATHS:
        for token in ("ana-test", "ben-test"):
            n, ms, size = measure(api, path, token)
            print(f"{path:28s} {token:9s} {n:6d} statements {ms:9.1f} ms {size / 1024:8.1f} KB")
            if os.environ.get("ANSWERS"):
                answer = api.get(path, headers=headers(token)).json()
                if isinstance(answer, dict):
                    answer.pop("seq", None)  # the change number the page is as of, not part of its rows
                answers[f"{path} {token}"] = answer
    if os.environ.get("ANSWERS"):
        Path(os.environ["ANSWERS"]).write_text(json.dumps(answers, sort_keys=True, default=str))
    if os.environ.get("STATEMENTS"):
        for token in ("ana-test", "ben-test"):
            seen = []
            with counting(api.app.state.store, seen):
                api.get(os.environ["STATEMENTS"], headers=headers(token))
            print(f"--- {token}: {len(seen)} statements")
            shapes = collections.Counter(re.sub(r"'[^']*'|\b\d+\b", "?", q)[:200] for q in seen)
            for q, k in shapes.most_common(25):
                print(f"{k:5d}  {q}")
    if os.environ.get("PROFILE"):
        import cProfile
        import pstats

        prof = cProfile.Profile()
        prof.enable()
        for _ in range(3):
            api.get(os.environ["PROFILE"], headers=headers("ana-test"))
        prof.disable()
        pstats.Stats(prof).sort_stats(os.environ.get("SORT", "cumulative")).print_stats(int(os.environ.get("TOP", 50)))


def reopened(folder, saved):
    """A client on a copy of a database this bench seeded before (DB=...), without seeding again."""
    from contextlib import closing

    from fastapi.testclient import TestClient

    from backend.app import create_app
    from backend.tests.helpers.api import api_settings

    settings = api_settings(folder)
    with closing(sqlite3.connect(Path(saved).as_uri() + "?mode=ro", uri=True)) as source:
        with closing(sqlite3.connect(settings.db_path)) as target:
            source.backup(target)
    app = create_app(settings)
    with TestClient(app) as client:
        yield client


def main():
    import tempfile

    from backend.tests.helpers.api_app import api_client

    keep = os.environ.get("DB")
    with tempfile.TemporaryDirectory(prefix=".tmp-overload-", dir=Path(__file__).resolve().parents[1]) as directory:
        folder = Path(directory)
        reuse = keep and Path(keep).exists()
        install = reopened(folder, keep) if reuse else api_client(folder, None)
        api = next(install)
        try:  # close the app even when seeding fails, or its threads keep the process alive
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
