"""Measures the bot reads on a fictional company the size of the hosted TIDY one (Nov 18 flight recorder: GET
/api/v2/bots 505 statements and 6.6 s a call, a bot's page 1.5 s, its tools 1.4-11 s, its files?limit=3 5-7 s):
165 bots on 4 computers, OPEN open tasks, a few hundred threads, some bots restricted, onboarding or on a cheaper task
model, a bot with FILES files and a company with TOOLS tools and their keys.

    nice -n 10 .venv/bin/python bench/perf-bots.py

Env: OPEN (default 1500), FILES (3000), TOOLS (40), RUNS (21), STATEMENTS=1 (the statement shapes each call repeats
most), PROFILE=<path> (cProfile one route), JSON=<file> (write every answer there, to compare two runs' output).

Per route and caller it prints the statements one call takes, the median wall time of RUNS calls and the answer's size.
Only fictional data, in a temporary folder removed afterwards. Never point it at a real hub.
"""

import collections
import contextlib
import json
import os
import random
import re
import shutil
import statistics
import sys
import time
from pathlib import Path

if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    import conftest  # noqa: F401  (the fictional registry and isolated benchmark settings)

from backend import bot_settings as BS, providers, threads
from backend.auth import Identity
from backend.store import H, Problem
from backend.tests.helpers import api as T
from backend.tests.helpers import engine as X
from backend.tests.helpers.api import headers

BOTS = [f"bot{n:03d}" for n in range(165)]
OPEN = int(os.environ.get("OPEN", 1500))
FILES = int(os.environ.get("FILES", 3000))
TOOLS = int(os.environ.get("TOOLS", 40))
RUNS = int(os.environ.get("RUNS", 21))
THREADS = 200
FILE_BOT = BOTS[1]
STAGES = (
    {"name": "Build", "owner": "assignee", "requirements": [{"kind": "owner_done"}]},
    {"name": "Review", "owner": "assignee", "requirements": [{"kind": "ok", "person": X.ANA, "line": "OK the build"}]},
)
CALLERS = ("ana-test", "cara-test", "bot-test")
PATHS = (
    tuple(os.environ["PATHS"].split())
    if os.environ.get("PATHS")
    else (
        "/api/v2/bots",
        "/api/v2/bots?include_archived=1",
        f"/api/v2/bots/{BOTS[5]}",
        f"/api/v2/bots/{FILE_BOT}/tools",
        f"/api/v2/bots/{FILE_BOT}/files?limit=3",
        "/api/employees",
    )
)


def seed(api):
    store = api.app.state.store
    store.settings.test_identities["bot-test"] = Identity("bot:" + BOTS[7], "bot", agent="hermes")
    rnd = random.Random(11)
    model = next(iter(providers.MODEL_BY_ID))
    with store.transaction() as c:
        for n, slug in enumerate(BOTS):
            X.bot(c, slug, computer=False)
            runner = f"r{n % 4}"
            c.execute(
                "INSERT OR IGNORE INTO runners(id,label,owner_id,token_hash,created_at) VALUES(?,?,'ana',?,?)",
                (runner, "box" + runner, "h" + runner, H.now()),
            )
            c.execute("INSERT OR REPLACE INTO assignments VALUES(?,?,1,?,'human:ana')", (slug, runner, H.now()))
            # A chart: every bot reports to the one ten places before it, the first ten to Ana.
            boss = "human:ana" if n < 10 else BOTS[n - 10]
            c.execute(
                "UPDATE bot_config SET reports_to=?, owner_id=?, bot_owners=? WHERE bot=?",
                (boss, ("ana", "ben", "cara")[n % 3], json.dumps(["ben"] if n % 5 == 0 else []), slug),
            )
            config = {"name": slug, "runtime": "fake", "status": "active"}
            if n % 8 == 3:
                config["task_model"] = {"model": model}
            BS.write(c, slug, config)
            if n % 6 == 2:
                T.restrict(
                    c, slug, see={"people": ["ana", "cara"]}, read={"people": ["ana"]}, write={"people": ["ana"]}
                )
            elif n % 6 == 4:
                T.restrict(c, slug, people=["ana"])
            if n % 15 == 7:
                c.execute("UPDATE bot_config SET onboarding_state='needs_setup' WHERE bot=?", (slug,))
            if n % 20 == 19:
                c.execute("UPDATE bots SET state='archived' WHERE id=?", (slug,))
        X.pipeline(c, "eng", "Engineering", STAGES, manager="bot:" + BOTS[0])
    tasks = collections.defaultdict(list)
    for start in range(0, OPEN, 200):
        with store.transaction() as c:
            for k in range(start, min(OPEN, start + 200)):
                owner = "bot:" + BOTS[k % len(BOTS)] if k % 8 else rnd.choice(["human:ana", "human:ben"])
                row = X.new_task(c, X.ANA, f"Open task {k}", "eng", assignee=owner)
                tasks[row["owner_id"]].append(row["id"])
                if k % 4 == 0:
                    X.ok_ask(c, row["id"], row["owner_id"], X.ANA, f"OK {k}")
    with store.transaction() as c:
        for n, slug in enumerate(BOTS):
            mine = tasks.get("bot:" + slug) or []
            if mine:
                H.status_set(c, "bot:" + slug, slug, state="idle", focus="Working", task_id=mine[0])
            if n % 9 == 0 and mine:
                c.execute("INSERT INTO bot_control(bot,draining,claim_task) VALUES(?,0,?)", (slug, mine[-1]))
    people = ["human:ana", "human:ben", "human:cara"]
    with store.transaction() as c:
        for n in range(THREADS):
            starter = rnd.choice(people + ["bot:" + b for b in BOTS[:30]])
            threads.post(c, starter, None, f"Thread {n} kickoff", start={"title": f"Topic {n}", "audience": "company"})
        for n in range(60):
            with contextlib.suppress(Problem):
                threads.post(c, "human:ana", None, f"Hi #{n}", start={"dm": ["bot:" + BOTS[n]], "title": ""})
    seed_files(store, tasks.get("bot:" + FILE_BOT) or [])
    seed_tools(store)


def seed_files(store, task_ids):
    """FILES files of FILE_BOT: most its own, a third on its tasks, one in ten with a second version."""
    now = H.now()
    with store.transaction() as c:
        c.execute(
            "INSERT INTO blobs(id,owner,digest,size,name,content_type,created_at) "
            "VALUES('bench','bot:" + FILE_BOT + "',?,1,'spec.html','text/html',?)",
            ("0" * 64, now),
        )
        rows = []
        for i in range(FILES):
            task = task_ids[i % len(task_ids)] if task_ids and i % 3 == 0 else None
            rows.append(
                (
                    f"file-{i:024x}",
                    FILE_BOT,
                    "task:" + task if task else "bot",
                    f"page-{i}",
                    f"Page {i}",
                    "file",
                    "text/html" if i % 4 else "image/png",
                    "tico_blob",
                    task,
                    2 if i % 10 == 0 else 1,
                    H.shift(now, minutes=-i),
                    H.shift(now, minutes=-i),
                )
            )
        c.executemany(
            "INSERT INTO bot_files(id,bot_id,scope,identity,title,kind,mime,locator,task_id,current_version,"
            "first_activity_at,last_activity_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
            rows,
        )
        c.executemany(
            "INSERT INTO bot_file_versions(file_id,version,blob_id,digest,size,name,mime,actor,created_at,repo_path) "
            "VALUES(?,?,'bench',?,1,?,'text/html',?,?,?)",
            [
                (r[0], v, "0" * 64, f"page-{i}.html", "bot:" + FILE_BOT, now, f"docs/page-{i}.html")
                for i, r in enumerate(rows)
                for v in range(1, r[9] + 1)
            ],
        )


def seed_tools(store):
    """TOOLS tools, each with a key; FILE_BOT holds a grant on half of them, and a few website logins."""
    now = H.now()
    with store.transaction() as c:
        for n in range(TOOLS):
            tool, key = f"tool{n:02d}", f"key{n:02d}"
            c.execute(
                "INSERT INTO tools(id,name,service,created_by,created_at,updated_at) VALUES(?,?,?,'human:ana',?,?)",
                (tool, f"Tool {n}", f"svc{n}", now, now),
            )
            c.execute(
                "INSERT INTO credentials(id,name,kind,env,tool_id,created_at,updated_at,updated_by,created_by) "
                "VALUES(?,?,'token',?,?,?,?,'ana','ana')",
                (key, f"Key {n}", f"TOOL{n}_TOKEN", tool, now, now),
            )
            if n % 2 == 0:
                c.execute(
                    "INSERT INTO credential_grants(id,credential_id,subject,granted_by,created_at) "
                    "VALUES(?,?,?,'human:ana',?)",
                    (H.new_id(), key, "bot:" + FILE_BOT, now),
                )
        for n in range(8):
            c.execute(
                "INSERT INTO credentials(id,name,kind,username,created_at,updated_at,updated_by,created_by,holder) "
                "VALUES(?,?,'login','someone',?,?,'ana','ana',?)",
                (f"login{n}", f"Site {n}", now, now, None if n % 2 else "bot:" + FILE_BOT),
            )


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


def measure(api, path, token):
    store, times, seen = api.app.state.store, [], []
    r = api.get(path, headers=headers(token))
    with counting(store, seen):
        api.get(path, headers=headers(token))
    for _ in range(RUNS):
        t = time.perf_counter()
        api.get(path, headers=headers(token))
        times.append((time.perf_counter() - t) * 1000)
    return r, seen, statistics.median(times)


def shape(sql):
    return re.sub(r"\s+", " ", re.sub(r"'[^']*'|\b\d+\b|\?(,\?)+", "?", sql))[:170]


def report(api):
    with api.app.state.store.read() as c:
        counts = {
            t: c.execute(f"SELECT count(*) FROM {t}").fetchone()[0]
            for t in ("bots", "tasks", "conversations", "messages", "bot_files", "credentials")
        }
    print(counts)
    answers = {}
    for path in PATHS:
        for token in CALLERS:
            r, seen, ms = measure(api, path, token)
            answers[f"{token} {path}"] = [r.status_code, r.json()]
            print(
                f"{path:44s} {token:9s} {r.status_code} {len(seen):6d} statements {ms:9.1f} ms {len(r.content) / 1024:8.1f} KB"
            )
            if os.environ.get("STATEMENTS"):
                for q, k in collections.Counter(shape(q) for q in seen).most_common(8):
                    if k > 2:
                        print(f"      {k:5d}  {q}")
    if os.environ.get("EXPLAIN"):
        explain(api)
    if os.environ.get("JSON"):
        Path(os.environ["JSON"]).write_text(json.dumps(answers, indent=1, sort_keys=True, default=str))
    if os.environ.get("PROFILE"):
        import cProfile
        import pstats

        prof = cProfile.Profile()
        prof.enable()
        for _ in range(3):
            api.get(os.environ["PROFILE"], headers=headers(os.environ.get("TOKEN", "ana-test")))
        prof.disable()
        pstats.Stats(prof).sort_stats(os.environ.get("SORT", "cumulative")).print_stats(int(os.environ.get("TOP", 50)))


def explain(api):
    """The plans of the reply-rule reads (teammate_access.replies) on the seeded tables."""
    from backend import teammate_access as TA

    targets = json.dumps(["bot:" + b for b in BOTS])
    with api.app.state.store.read() as c:
        for name in ("_WROTE_SQL", "_ASKED_SQL"):
            sql = getattr(TA, name, None)
            if sql:
                plan = [row[3] for row in c.execute("EXPLAIN QUERY PLAN " + sql, (targets, "bot:" + BOTS[7]))]
                print(name, "|", " / ".join(plan))


def compare(a, b):
    """Two runs' answers (JSON=) the same, but for ids (uuids) and times, which each seeding draws anew."""
    norm = lambda text: re.sub(
        r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}",
        "ID",
        re.sub(r"\d{4}-\d\d-\d\dT[\d:.]+Z?|\b\d+\.\d+\b", "T", text),
    )
    left, right = (json.loads(Path(f).read_text()) for f in (a, b))
    items = lambda value: sorted(norm(json.dumps(item, sort_keys=True)) for item in (value or [None, []])[1] or [])
    for key in sorted(set(left) | set(right)):
        one, two = (norm(json.dumps(side.get(key), sort_keys=True)) for side in (left, right))
        # A list's rows in another order: rows tied on a time sort by their (random) ids.
        same = one == two or (
            isinstance((left.get(key) or [0, 0])[1], list) and items(left.get(key)) == items(right.get(key))
        )
        print(("same     " if one == two else "reordered" if same else "DIFFERENT"), key)


def main():
    if os.environ.get("COMPARE"):
        return compare(*os.environ["COMPARE"].split(","))
    import tempfile

    from backend.tests.helpers.api_app import api_client

    with tempfile.TemporaryDirectory(prefix=".tmp-bots-", dir=Path(__file__).resolve().parents[1]) as directory:
        folder = Path(directory)
        install = api_client(folder, None)
        api = next(install)
        t = time.perf_counter()
        seed(api)
        print(f"seeded in {time.perf_counter() - t:.1f}s")
        try:
            report(api)
        finally:
            install.close()
            shutil.rmtree(folder, ignore_errors=True)


if __name__ == "__main__":
    main()
