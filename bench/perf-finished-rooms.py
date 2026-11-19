"""Measures the live reads that walk finished tasks' rooms, on a fictional company of the hosted TIDY hub's shape (Nov
19): OPEN open tasks, CLOSED finished ones (94% of all), every task with its own room, Ana following FOLLOWED of the
finished rooms and every open one, REVIVED finished rooms with a person's post after the task finished, UNREAD finished
rooms with a bot's post Ana has not read from before it finished, and a few hundred threads with posts.

    nice -n 10 .venv/bin/python bench/perf-finished-rooms.py

Reads, each as Ana and as Ben: the rail, Catch up and its count, the sidebar, Needs you, Health, the Floor, and the live
stream's setup (Needs you's baseline) and one round after a handful of writes. Per read it prints the statements one call
takes and the median wall time of RUNS calls. Env as bench/perf-hub-overload.py (OPEN, CLOSED, THREADS, RUNS, DB=<file>
to keep the seeded database and reuse it, ANSWERS=<file> for each read's answer as JSON, STATEMENTS=<path> for the
slowest statements of one read), plus FOLLOWED (6100), REVIVED (350), UNREAD (30), and RESAVE=<file> (with DB: save the
copy as one start of the app leaves it, and measure nothing).

DB= on a checkout with a pending migration applies it on open, so the same seeded database measures before and after a
schema change. Only fictional data, in a temporary folder removed afterwards (unless DB is given). Never point it at a
real hub.
"""

import contextlib
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

from backend import events as E
from backend import threads
from backend.store import H, Problem
from backend.tests.helpers.api import headers

_spec = importlib.util.spec_from_file_location("overload", ROOT / "bench" / "perf-hub-overload.py")
O = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(O)

os.environ.setdefault("OPEN", "1150")
os.environ.setdefault("CLOSED", "17350")
O.OPEN, O.CLOSED = int(os.environ["OPEN"]), int(os.environ["CLOSED"])
O.THREADS = int(os.environ.get("THREADS", 300))
RUNS = O.RUNS = int(os.environ.get("RUNS", 5))
FOLLOWED = int(os.environ.get("FOLLOWED", 6100))
REVIVED = int(os.environ.get("REVIVED", 350))
UNREAD = int(os.environ.get("UNREAD", 30))
ROUND_CHANGES = 40  # the changes one round reads, back from the newest
PATHS = (
    "/api/v2/threads/rail",
    "/api/v2/threads/catch-up",
    "/api/v2/threads?limit=50",
    "/api/v2/needs-you",
    "/api/v2/health",
    "/api/v2/company/floor",
)
TOKENS = ("ana-test", "ben-test")
CAPTURED = {}


def _capture_task_views():
    """The app's task_views, which the live stream renders task rows with, kept for the stream reads below."""
    original = E.install

    def install(app, store, auth, task_views, task_view):
        CAPTURED["task_views"] = task_views
        return original(app, store, auth, task_views, task_view)

    E.install = install


def seed(api):
    O.seed(api)
    store, rnd = api.app.state.store, random.Random(19)
    with store.transaction() as c:
        # The finished tasks: most Closed, as TIDY's history is; their finish an hour ago, before any post below.
        for (tid,) in c.execute("SELECT id FROM tasks WHERE status='done'").fetchall():
            if rnd.random() < 0.8:
                c.execute("INSERT OR REPLACE INTO task_move_guards (id, open, task_id) VALUES (1, 1, ?)", (tid,))
                c.execute("UPDATE tasks SET status='closed' WHERE id=?", (tid,))
                c.execute("UPDATE task_move_guards SET open=0, task_id=NULL WHERE id=1")
        ago = H.shift(H.now(), hours=-1)
        c.execute("UPDATE tasks SET closed_at=?, done_at=?, updated_at=? WHERE status IN ('done','closed')", (ago,) * 3)
        finished = [
            r[0]
            for r in c.execute(
                "SELECT r.id FROM conversations r JOIN tasks t ON t.id=r.task_id WHERE r.parent_id IS NULL "
                "AND t.status IN ('done','closed') ORDER BY r.id"
            )
        ]
        open_rooms = [
            r[0]
            for r in c.execute(
                "SELECT r.id FROM conversations r JOIN tasks t ON t.id=r.task_id WHERE r.parent_id IS NULL "
                "AND t.status='open'"
            )
        ]
        rnd.shuffle(finished)
        followed = finished[:FOLLOWED] + open_rooms
        now = H.now()
        c.executemany(
            "INSERT OR IGNORE INTO thread_followers(id,thread_id,actor,following,notify,reason,last_read_seq,"
            "created_at,updated_at) VALUES(?,?,'human:ana',1,'live','task',0,?,?)",
            [(H.new_id(), room, now, now) for room in followed],
        )
        c.executemany(
            "INSERT OR IGNORE INTO thread_followers(id,thread_id,actor,following,notify,reason,last_read_seq,"
            "created_at,updated_at) VALUES(?,?,'human:ben',1,'live','task',0,?,?)",
            [(H.new_id(), room, now, now) for room in finished[: FOLLOWED // 3]],
        )
    # Unread from before the finish: a bot's post, then the task's finish moved past it.
    with store.transaction() as c:
        for room in finished[REVIVED : REVIVED + UNREAD]:
            with contextlib.suppress(Problem, H.Refused):
                threads.post(c, "bot:" + O.BOTS[1], room, "Done: see the notes @ana")
            c.execute(
                "UPDATE tasks SET closed_at=?, done_at=?, updated_at=? WHERE id=(SELECT task_id FROM conversations "
                "WHERE id=?)",
                (H.shift(H.now(), seconds=1),) * 3 + (room,),
            )
    # Revived: a person's post after the finish brings the room back. Last, what a stream's round reads: a post in an
    # open task's room and a change to that task.
    for start in range(0, REVIVED, 100):
        with store.transaction() as c:
            for room in finished[start : min(REVIVED, start + 100)]:
                with contextlib.suppress(Problem, H.Refused):
                    threads.post(c, rnd.choice(["human:ben", "human:cara"]), room, "One more thing on this")
    with store.transaction() as c:
        room = c.execute(
            "SELECT r.id FROM conversations r JOIN tasks t ON t.id=r.task_id WHERE t.status='open' ORDER BY r.id LIMIT 1"
        ).fetchone()[0]
        threads.post(c, "human:ben", room, "Round check @ana")
        c.execute("UPDATE tasks SET title=title||' ' WHERE id=(SELECT task_id FROM conversations WHERE id=?)", (room,))


def _stream_reads(api, token, after):
    """(statements, ms) of the stream's setup (Needs you's baseline and the latest change) and of one round's read."""
    store, auth = api.app.state.store, api.app.state.auth
    who = auth.authenticate({"authorization": "Bearer " + token})
    task_view, task_views = api.app.state.task_view, CAPTURED.get("task_views")

    def setup():
        with store.read() as c:
            held = E.needs_baseline(c, auth, who, task_view)
            return held, E.latest(c)

    def round_(held):
        with store.read() as c:
            return E.read(
                c,
                auth,
                who,
                after,
                task_views=task_views,
                task_view=task_view,
                needs_held=dict(held),
                limit=E.ROUND_ROWS,
            )

    held, _ = setup()
    out = {}
    for name, call in (("events setup", setup), ("events round", lambda: round_(held))):
        seen, times = [], []
        with O.counting(store, seen):
            call()
        for _ in range(RUNS):
            t = time.perf_counter()
            call()
            times.append((time.perf_counter() - t) * 1000)
        out[name] = (len(seen), statistics.median(times))
    return out


def report(api):
    store = api.app.state.store
    with store.read() as c:
        counts = {
            t: c.execute(f"SELECT count(*) FROM {t}").fetchone()[0]
            for t in ("tasks", "conversations", "messages", "thread_followers")
        }
        # What a round reads: the changes of the last writes the seed made (`ROUND_CHANGES`), the same on every copy.
        after = max(0, E.latest(c) - ROUND_CHANGES)
    print(counts)
    answers = {}
    for token in TOKENS:
        for path in PATHS:
            n, ms, size = O.measure(api, path, token)
            print(f"{path:32s} {token:9s} {n:6d} statements {ms:9.1f} ms {size / 1024:8.1f} KB")
            if os.environ.get("ANSWERS"):
                answer = api.get(path, headers=headers(token)).json()
                if isinstance(answer, dict):
                    answer.pop("seq", None)
                    answer.pop("version", None)
                answers[f"{path} {token}"] = answer
        for name, (n, ms) in _stream_reads(api, token, after).items():
            print(f"{name:32s} {token:9s} {n:6d} statements {ms:9.1f} ms")
    if os.environ.get("ANSWERS"):
        Path(os.environ["ANSWERS"]).write_text(json.dumps(answers, sort_keys=True, default=str))
    if os.environ.get("STATEMENTS"):
        O.TOKENS = TOKENS
        O.PATHS = ()
        O.report(api)


def main():
    from backend.tests.helpers.api_app import api_client

    _capture_task_views()
    keep = os.environ.get("DB")
    with tempfile.TemporaryDirectory(prefix=".tmp-finished-rooms-", dir=ROOT) as directory:
        folder = Path(directory)
        reuse = keep and Path(keep).exists()
        t = time.perf_counter()
        install = O.reopened(folder, keep) if reuse else api_client(folder, None)
        api = next(install)
        if reuse:
            print(f"opened (pending migrations applied) in {time.perf_counter() - t:.1f}s")
        try:
            if not reuse:
                t = time.perf_counter()
                seed(api)
                print(f"seeded in {time.perf_counter() - t:.1f}s")
                if keep:
                    with api.app.state.store.read() as c:
                        c.execute("VACUUM INTO ?", (keep,))
            if os.environ.get("RESAVE"):
                # A copy as one start of the app and one of each read leave it (what they make on first use
                # included), so two checkouts measured on it start from the same rows and their answers compare.
                for token in TOKENS:
                    for path in PATHS:
                        api.get(path, headers=headers(token))
                    _stream_reads(api, token, 0)
                with api.app.state.store.read() as c:
                    c.execute("VACUUM INTO ?", (os.environ["RESAVE"],))
                return
            report(api)
        finally:
            install.close()
            shutil.rmtree(folder, ignore_errors=True)


if __name__ == "__main__":
    main()
