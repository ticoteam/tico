"""Measures the rail, Catch up and Browse on a fictional company of the hosted TIDY hub's shape (Nov 19: 19k roots,
17.5k of them finished tasks' rooms, one person following 6.5k of those; a rehearsal copy's Catch up took 6.6 s for
limit=20). It seeds what bench/perf-hub-overload.py does (each finished task's room made its own root), then finishes
most tasks an hour ago, has Ana follow FOLLOWED finished rooms and every open one, REVIVED finished rooms get a person's
post after the finish, UNREAD get a bot's post from before the finish, and HIDDEN get a post after the finish that only
a bot may read (restricted to it), which must not bring the room back for Ana.

    nice -n 10 .venv/bin/python bench/perf-catch-up.py

Env as bench/perf-hub-overload.py (OPEN, CLOSED, THREADS, RUNS), plus FOLLOWED (6500), REVIVED (350), UNREAD (30),
HIDDEN (40), DB=<file> (keep the seeded database there and reuse it: a pending migration is applied on open, so one
seeded database measures before and after a schema change), ANSWERS=<file> (each read's answer as JSON),
STATEMENTS=<path> (the statement shapes and slowest statements of one read), PROFILE=<path> (cProfile one read).

Reads, as Ana and as Ben: the rail, Catch up (the default page, limit=20, and the page after limit=20's cursor), its
count form when the route has one, and Browse. Per read it prints the statements one call takes, the median wall time
of RUNS calls and the answer's size. Only fictional data, in a temporary folder removed afterwards (unless DB is
given). Never point it at a real hub.
"""

import contextlib
import importlib.util
import json
import os
import random
import shutil
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if __name__ == "__main__":
    sys.path.insert(0, str(ROOT))
    import conftest  # noqa: F401  (the fictional registry and isolated benchmark settings)

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
FOLLOWED = int(os.environ.get("FOLLOWED", 6500))
REVIVED = int(os.environ.get("REVIVED", 350))
UNREAD = int(os.environ.get("UNREAD", 30))
HIDDEN = int(os.environ.get("HIDDEN", 40))
TOKENS = ("ana-test", "ben-test")


def seed(api):
    O.seed(api)
    store, rnd = api.app.state.store, random.Random(19)
    with store.transaction() as c:
        # perf-hub-overload copies each finished task's room with its source's root_id and path: make each its own root.
        c.execute("UPDATE conversations SET root_id=id, path='/'||id||'/' WHERE parent_id IS NULL AND root_id<>id")
        # ...and with its source's counters: a copy holds no messages, so nothing in it is unread or active.
        c.execute(
            "UPDATE conversations SET last_seq=0, last_unread_seq=0, last_message_at=NULL "
            "WHERE NOT EXISTS (SELECT 1 FROM messages m WHERE m.conversation_id=conversations.id)"
        )
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
        now = H.now()
        c.executemany(
            "INSERT OR IGNORE INTO thread_followers(id,thread_id,actor,following,notify,reason,last_read_seq,"
            "created_at,updated_at) VALUES(?,?,'human:ana',1,'live','task',0,?,?)",
            [(H.new_id(), room, now, now) for room in finished[:FOLLOWED] + open_rooms],
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
    # Revived: a person's post after the finish brings the room back.
    for start in range(0, REVIVED, 100):
        with store.transaction() as c:
            for room in finished[start : min(REVIVED, start + 100)]:
                with contextlib.suppress(Problem, H.Refused):
                    threads.post(c, rnd.choice(["human:ben", "human:cara"]), room, "One more thing on this")
    # After the finish, a post only one bot may read: it brings the room back for nobody else.
    with store.transaction() as c:
        for room in finished[REVIVED + UNREAD : REVIVED + UNREAD + HIDDEN]:
            with contextlib.suppress(Problem, H.Refused):
                out = threads.post(c, "bot:" + O.BOTS[2], room, "Private follow-up")
                mid = out.get("id") or out.get("message_id")
                if mid:
                    c.execute(
                        "UPDATE messages SET restricted=1, legacy_audience=? WHERE id=?",
                        (json.dumps(["bot:" + O.BOTS[2]]), mid),
                    )


def _paths(api, token):
    """The reads to measure as this reader: the page after limit=20's cursor among them, when there is one."""
    paths = [
        "/api/v2/threads/rail",
        "/api/v2/threads/catch-up",
        "/api/v2/threads/catch-up?limit=20",
    ]
    first = api.get("/api/v2/threads/catch-up?limit=20", headers=headers(token)).json()
    if first.get("next_cursor"):
        paths.append(f"/api/v2/threads/catch-up?limit=20&cursor={first['next_cursor']}")
    if api.get("/api/v2/threads/catch-up/count", headers=headers(token)).status_code == 200:
        paths.append("/api/v2/threads/catch-up/count")
    paths.append("/api/v2/threads/browse")
    return paths


def report(api):
    store = api.app.state.store
    with store.read() as c:
        counts = {
            t: c.execute(f"SELECT count(*) FROM {t}").fetchone()[0]
            for t in ("tasks", "conversations", "messages", "thread_followers")
        }
        counts["roots"] = c.execute("SELECT count(*) FROM conversations WHERE parent_id IS NULL").fetchone()[0]
        counts["finished rooms"] = c.execute(
            "SELECT count(*) FROM conversations r JOIN tasks t ON t.id=r.task_id WHERE r.parent_id IS NULL "
            "AND t.status IN ('done','closed')"
        ).fetchone()[0]
    print(counts)
    answers = {}
    for token in TOKENS:
        for path in _paths(api, token):
            n, ms, size = O.measure(api, path, token)
            label = path.split("&cursor=")[0] + ("&cursor=<next>" if "&cursor=" in path else "")
            print(f"{label:44s} {token:9s} {n:6d} statements {ms:9.1f} ms {size / 1024:8.1f} KB")
            if os.environ.get("ANSWERS"):
                answer = api.get(path, headers=headers(token)).json()
                if isinstance(answer, dict):
                    answer.pop("seq", None)
                answers[f"{label} {token}"] = answer
    if os.environ.get("ANSWERS"):
        Path(os.environ["ANSWERS"]).write_text(json.dumps(answers, sort_keys=True, default=str))
    if os.environ.get("STATEMENTS") or os.environ.get("PROFILE"):
        O.TOKENS = TOKENS
        O.PATHS = ()
        O.report(api)


def main():
    from backend.tests.helpers.api_app import api_client

    keep = os.environ.get("DB")
    with tempfile.TemporaryDirectory(prefix=".tmp-catch-up-", dir=ROOT) as directory:
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
            report(api)
        finally:
            install.close()
            shutil.rmtree(folder, ignore_errors=True)


if __name__ == "__main__":
    main()
