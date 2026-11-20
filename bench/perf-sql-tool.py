"""Measures POST /api/v2/sql at the hosted TIDY company's size (Nov 19: 699 calls in 2 h, 1.6 s average, p95 2.2 s,
about 36 statements and 50-60 ms of SQL each; 18.6k tasks with 1,150 open, 18.7k rooms, 39k messages) on
bench/perf-hub-overload.py's fictional company, with a message history added to its task rooms and a few private tasks.

    nice -n 10 .venv/bin/python bench/perf-sql-tool.py

Env: as perf-hub-overload.py (OPEN, CLOSED, THREADS, RUNS, DB=<file> to keep and reuse the seeded database), plus
MESSAGES (default 36000 added), ANSWERS=<file> (every statement's answer per reader as JSON, to compare two
checkouts on one DB), PROFILE=1 (cProfile three calls by the regular person) and TOP (rows of the profile).

The readers are the owner (ana), a regular person (cara) and cara's Mac runner. Only fictional data, in a temporary
folder removed afterwards (unless DB is given). Never point it at a real hub.
"""

import contextlib
import importlib.util
import json
import os
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

from backend.store import H
from backend.tests.helpers.api import headers

RUNS = int(os.environ.get("RUNS", 7))
MESSAGES = int(os.environ.get("MESSAGES", 36000))
STATEMENTS = (
    "SELECT id, title, status FROM tasks WHERE status='open' ORDER BY created_at DESC LIMIT 50",
    "SELECT count(*) FROM tasks",
    "SELECT count(*) FROM messages",
    "SELECT id, conversation_id, body FROM messages ORDER BY created_at DESC LIMIT 20",
    "SELECT c.id, c.subject, count(m.id) FROM conversations c JOIN messages m ON m.conversation_id=c.id "
    "GROUP BY c.id ORDER BY 3 DESC LIMIT 10",
    "SELECT id, subject FROM conversations WHERE task_id IS NULL ORDER BY created_at DESC LIMIT 20",
    "SELECT id, display_name FROM bots ORDER BY id LIMIT 10",
    "SELECT action, count(*) FROM events GROUP BY action ORDER BY 2 DESC LIMIT 10",
    "SELECT t.id, t.title, w.state FROM tasks t JOIN task_waits w ON w.task_id=t.id LIMIT 20",
    "SELECT * FROM run_weekly LIMIT 5",
)


def seed(api):
    overload.seed(api)
    store = api.app.state.store
    with store.transaction() as c:
        rooms = [r[0] for r in c.execute("SELECT id FROM conversations WHERE task_id IS NOT NULL ORDER BY rowid")]
        seq = c.execute("SELECT coalesce(max(seq), 0) FROM messages").fetchone()[0]
        senders = ["human:ana", "human:ben", "human:cara"] + ["bot:" + b for b in overload.BOTS[:40]]
        rows = []
        for n in range(MESSAGES):
            room = rooms[(n * 7) % len(rooms)]
            seq += 1
            rows.append(
                (H.new_id(), room, senders[n % len(senders)], "chat", f"Update {n} on the work", "{}", H.now(), seq)
            )
        c.executemany(
            "INSERT INTO messages(id,conversation_id,sender_id,kind,body,refs,created_at,seq) VALUES(?,?,?,?,?,?,?,?)",
            rows,
        )
        # A few private tasks the regular person is no party to.
        c.execute(
            "UPDATE tasks SET private=1 WHERE id IN (SELECT id FROM tasks WHERE owner_id LIKE 'bot:%' "
            "ORDER BY rowid LIMIT 150)"
        )
    with store.read() as c:
        api.app.state.auth.sync_access(c)


def mac(api):
    from backend.tests.helpers.api import post

    code = post(api, "enrollments", {"operator": "cara"}, token="ana-test")["code"]
    return post(api, "runners/enroll", {"code": code, "label": "Cara's Mac", "platform": "darwin"})["token"]


@contextlib.contextmanager
def traced(seen):
    """Count the statements the SQL tool's own connection runs too, not only the store's."""
    from backend import sql

    original = sql.connect
    sql.connect = lambda *a, **k: original(*a, **{**k, "trace": seen.append})
    try:
        yield
    finally:
        sql.connect = original


def call(api, token, statement):
    r = api.post("/api/v2/sql", json={"sql": statement}, headers=headers(token))
    return r.status_code, r.json()


def report(api):
    store = api.app.state.store
    with store.read() as c:
        counts = {
            t: c.execute(f"SELECT count(*) FROM {t}").fetchone()[0] for t in ("tasks", "conversations", "messages")
        }
        counts["open"] = c.execute("SELECT count(*) FROM tasks WHERE status='open'").fetchone()[0]
    print(counts)
    readers = {"owner": "ana-test", "person": "cara-test", "mac": mac(api)}
    answers, all_times = {}, []
    for label, token in readers.items():
        for statement in STATEMENTS:
            status, body = call(api, token, statement)
            answers[f"{label} {statement}"] = [status, {k: v for k, v in body.items() if k != "ms"}]
            seen = []
            with overload.counting(store, seen), traced(seen):
                call(api, token, statement)
            times = []
            for _ in range(RUNS):
                t = time.perf_counter()
                call(api, token, statement)
                times.append((time.perf_counter() - t) * 1000)
            all_times += times
            print(
                f"{label:6s} {status} {len(seen):4d} stmts median {statistics.median(times):7.1f} ms "
                f"max {max(times):7.1f} ms  {statement[:70]}"
            )
    all_times.sort()
    print(
        f"all calls: median {statistics.median(all_times):.1f} ms, p95 {all_times[int(len(all_times) * 0.95)]:.1f} ms"
    )
    if os.environ.get("ANSWERS"):
        Path(os.environ["ANSWERS"]).write_text(json.dumps(answers, sort_keys=True, default=str, indent=1))
    if os.environ.get("PROFILE"):
        import cProfile
        import pstats

        prof = cProfile.Profile()
        prof.enable()
        for _ in range(3):
            call(api, "cara-test", STATEMENTS[0])
        prof.disable()
        pstats.Stats(prof).sort_stats(os.environ.get("SORT", "cumulative")).print_stats(int(os.environ.get("TOP", 60)))


def main():
    from backend.tests.helpers.api_app import api_client

    keep = os.environ.get("DB")
    with tempfile.TemporaryDirectory(prefix=".tmp-sql-tool-", dir=ROOT) as directory:
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
