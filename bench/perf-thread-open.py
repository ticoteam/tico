"""Measures opening a chat: GET /api/v2/threads/{id} (the thread header), GET /api/v2/conversations/{id}/snapshot (a
DM's newest messages and its bot's run) and GET /api/v2/conversations (the caller's, and the owner's view of a bot's),
on a fictional busy company: 40 bots, a thread with every bot in it, sub-threads, DMs whose bot replies each come from a
run, a few hundred tasks with their threads, and open ❓ asks on them.

    nice -n 19 python3 bench/perf-thread-open.py seed DIR         # build the company once, into DIR/hub.db
    nice -n 19 python3 bench/perf-thread-open.py measure DIR OUT  # each read on a copy of it; writes OUT (JSON)
    python3 bench/perf-thread-open.py compare BEFORE AFTER        # the table, and whether every answer is the same

`measure` runs on a copy of the seeded file, so two checkouts (main and a branch) read the very same rows: run `seed`
once, then `measure` from each checkout. Per read it records the statements one call runs, the most repeated ones, any
write among them, the median wall time of RUNS calls, and the answer itself.
"""

import collections
import contextlib
import json
import random
import re
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

from fastapi.testclient import TestClient

from backend import question_marks, threads
from backend.app import create_app
from backend.store import H, Problem
from backend.tests.helpers.api import api_settings, headers
from backend.tests.helpers.api_app import api_client

BOTS = [f"bot{n:02d}" for n in range(40)]
PEOPLE = ["human:ana", "human:ben", "human:cara"]
TASKS = 300
ASKS = 200
DM_REPLIES = 120
RUNS = 20
WRITES = re.compile(r"^\s*(INSERT|UPDATE|DELETE|REPLACE)\b", re.I)


def _run(c, key, bot, message_id):
    """A completed run of `bot` woken by `message_id`; its id."""
    aid = "run-" + key
    c.execute(
        "INSERT INTO jobs(id,message_id,bot_id,state,created_at,attempt_id) VALUES(?,?,?,'completed',?,?)",
        ("job-" + key, message_id, bot, H.now(), aid),
    )
    c.execute(
        "INSERT INTO attempts(id,job_id,bot_id,runner_id,generation,token_hash,state,lease_until,created_at,"
        "started_at,finished_at) VALUES(?,?,?,'rn',0,?,'completed',?,?,?,?)",
        (aid, "job-" + key, bot, aid, H.now(), H.now(), H.now(), H.now()),
    )
    c.execute("INSERT INTO attempt_inputs(attempt_id,message_id) VALUES(?,?)", (aid, message_id))
    return aid


def seed(api):
    """The company; {name: path} of the reads to measure."""
    store = api.app.state.store
    rnd = random.Random(11)
    with store.transaction() as c:
        H.sync_registry(
            c,
            {
                s: {"name": s, "runtime": "fake", "status": "active"}
                for s in BOTS + ["assistant", "ops", "cpo", "product-design", "finance", "inbox", "doc-updater"]
            },
            {"people": [{"id": p, "email": p + "@acme.example"} for p in ("ana", "ben", "cara")]},
        )
        for s in BOTS:
            c.execute("INSERT OR IGNORE INTO bot_config(bot,owner_id) VALUES(?,'ana')", (s,))
        c.execute(
            "INSERT INTO runners(id,label,owner_id,token_hash,created_at) VALUES('rn','Mac','ana','th',?)", (H.now(),)
        )
    # The busy thread: every bot and person in it, a long history, sub-threads under some of its messages.
    with store.transaction() as c:
        out = threads.post(c, "human:ana", None, "Launch plan", start={"title": "Launch", "audience": "members"})
        busy = out.get("thread_id") or out.get("conversation_id")
        for s in BOTS:
            threads.add_member(c, "human:ana", busy, "bot:" + s)
        for p in PEOPLE[1:]:
            threads.add_member(c, "human:ana", busy, p)
        posted = []
        for k in range(200):
            who = rnd.choice(PEOPLE + ["bot:" + b for b in BOTS])
            with contextlib.suppress(Problem):
                posted.append(threads.post(c, who, busy, f"Note {k}" + (" @ana" if k % 7 == 0 else ""))["id"])
        sub = None
        for mid in posted[:30:3]:
            sub = threads.sub_thread(c, "human:ana", mid)["id"]
            for k in range(5):
                threads.post(c, rnd.choice(PEOPLE + ["bot:" + b for b in BOTS[:10]]), sub, f"Reply {k}")
    # A DM with a bot whose replies each name their run and a task, and many more DMs and threads with that bot.
    tasks = []
    with store.transaction() as c:
        for n in range(TASKS):
            requester = rnd.choice(PEOPLE + ["bot:assistant", "bot:" + rnd.choice(BOTS)])
            t = H.task_create(c, requester, f"Task {n}", "", rnd.choice(PEOPLE), lint=False)
            tasks.append(t["id"])
            if n % 3 == 0:
                H.task_done(c, "human:ana", t["id"], "Done.", seen_version=H.task(c, t["id"])["version"])
    with store.transaction() as c:
        out = threads.post(c, "human:ana", None, "Hi assistant", start={"dm": ["bot:assistant"], "title": ""})
        dm = out.get("thread_id") or out.get("conversation_id")
        for k in range(DM_REPLIES):
            asked = threads.post(c, "human:ana", dm, f"Question {k}, see task {tasks[k]}")
            aid = _run(c, f"dm{k}", "assistant", asked["id"])
            threads.post(
                c,
                "bot:assistant",
                dm,
                f"Answer {k}",
                in_reply_to=asked["id"],
                refs={"turn_id": aid, "run": {"attempt_id": aid, "job_id": f"job-dm{k}"}, "task": tasks[k]},
            )
        for who in ("human:ben", "human:cara"):
            threads.post(c, who, None, f"Hi from {who}", start={"dm": ["bot:assistant"], "title": ""})
        for n in range(60):
            out = threads.post(
                c, "bot:assistant", None, f"Thread {n}", start={"title": f"Topic {n}", "audience": "company"}
            )
            tid = out.get("thread_id") or out.get("conversation_id")
            threads.post(c, rnd.choice(PEOPLE), tid, "Thanks")
        for n in range(30):
            out = threads.post(c, "human:ana", None, f"Hi {n}", start={"dm": ["bot:" + BOTS[n]], "title": ""})
    # Open asks on tasks: the open-❓ read the snapshot's run view and every task check share.
    with store.transaction() as c:
        for n in range(ASKS):
            with contextlib.suppress(Problem):
                question_marks.ask(c, "bot:assistant", tasks[n], rnd.choice(PEOPLE), f"Which one, {n}?", checked=False)
    with store.transaction() as c:
        [task_thread] = c.execute(
            "SELECT id FROM conversations WHERE task_id=? AND parent_id IS NULL", (tasks[1],)
        ).fetchone()
    return {
        "thread_busy": f"/api/v2/threads/{busy}",
        "thread_sub": f"/api/v2/threads/{sub}",
        "thread_dm": f"/api/v2/threads/{dm}",
        "thread_task": f"/api/v2/threads/{task_thread}",
        "snapshot_dm": f"/api/v2/conversations/{dm}/snapshot?limit=50&max_bytes=200000",
        "conversations_bot": "/api/v2/conversations?bot=assistant",
        "conversations_mine": "/api/v2/conversations",
    }


def _counted(store):
    """Trace every statement run on a borrowed connection (store.read and read_transaction both borrow one)."""
    seen, take, give = [], store._take, store._give

    def traced():
        c = take()
        c.set_trace_callback(seen.append)
        return c

    def untraced(c):
        c.set_trace_callback(None)
        give(c)

    return seen, traced, untraced


def _shape(sql):
    return re.sub(r"\s+", " ", re.sub(r"'[^']*'|\b\d+\b", "?", sql)).strip()[:180]


def measure(api, paths):
    store, out = api.app.state.store, {}
    for name, path in paths.items():
        answer = api.get(path, headers=headers("ana-test"))
        assert answer.status_code == 200, (path, answer.text[:300])
        take, give = store._take, store._give
        seen, traced, untraced = _counted(store)
        store._take, store._give = traced, untraced
        try:
            api.get(path, headers=headers("ana-test"))
        finally:
            store._take, store._give = take, give
        out[name] = {"path": path, "statements": len(seen)}
        out[name]["writes"] = sorted({_shape(s) for s in seen if WRITES.match(s)})
        out[name]["top"] = collections.Counter(_shape(s) for s in seen).most_common(12)
        times = []
        for _ in range(RUNS):
            t = time.perf_counter()
            api.get(path, headers=headers("ana-test"))
            times.append((time.perf_counter() - t) * 1000)
        out[name]["median_ms"] = round(statistics.median(times), 1)
        out[name]["answer"] = answer.json()
    return out


def _app(directory, db):
    settings = api_settings(directory)
    shutil.copyfile(db, settings.db_path)
    return TestClient(create_app(settings))


def main(argv):
    mode = argv[1] if len(argv) > 1 else ""
    if mode == "seed":
        target = Path(argv[2])
        target.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix=".tmp-open-", dir=ROOT) as directory:
            install = api_client(Path(directory), None)
            api = next(install)
            t = time.perf_counter()
            paths = seed(api)
            print(f"seeded in {time.perf_counter() - t:.1f}s")
            with api.app.state.store.read() as c:
                c.execute("PRAGMA wal_checkpoint(TRUNCATE)")
                counts = {
                    n: c.execute(f"SELECT count(*) FROM {n}").fetchone()[0]
                    for n in ("conversations", "messages", "tasks", "question_marks", "thread_members", "attempts")
                }
            print(counts)
            with contextlib.closing(api.app.state.store.connect()) as c:
                c.execute("VACUUM INTO ?", (str(target / "hub.db"),))
            install.close()
        (target / "paths.json").write_text(json.dumps(paths))
        return 0
    if mode == "measure":
        source, report = Path(argv[2]), Path(argv[3])
        paths = json.loads((source / "paths.json").read_text())
        with tempfile.TemporaryDirectory(prefix=".tmp-open-", dir=ROOT) as directory:
            with _app(Path(directory), source / "hub.db") as api:
                result = measure(api, paths)
        report.write_text(json.dumps(result, indent=1, default=str))
        for name, r in result.items():
            print(f"{name:20s} {r['statements']:5d} statements {r['median_ms']:8.1f} ms  writes={r['writes']}")
            for shape, n in r["top"][:8]:
                print(f"    {n:4d}  {shape}")
        return 0
    if mode == "compare":
        before, after = (json.loads(Path(p).read_text()) for p in argv[2:4])
        print("| read | statements before | after | median ms before | after | same answer |")
        print("|---|---|---|---|---|---|")
        same_all = True
        for name in before:
            b, a = before[name], after[name]
            same = b["answer"] == a["answer"]
            same_all &= same
            print(
                f"| {name} | {b['statements']} | {a['statements']} | {b['median_ms']} | {a['median_ms']} | "
                f"{'yes' if same else 'NO'} |"
            )
            if not same:
                print("   before:", json.dumps(b["answer"], default=str)[:1500])
                print("   after: ", json.dumps(a["answer"], default=str)[:1500])
        return 0 if same_all else 1
    print(__doc__)
    return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
