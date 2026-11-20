"""Measures four list reads on a fictional company the size of the hosted TIDY one (Nov 19 flight recorder: GET
/api/v2/automations 792 calls in 3 h, GET /api/v2/notes 420 ms a call): the Automations list with its pages, the
notes list, the Dashboard's default cards (tasks by status, done per week) and the Done archive (`sort=finished`) with
its next page. TASKS tasks (OPEN of them open, the rest done or closed over the past year), AUTOS automations owned by
people and agents, NOTES notes between agents, a third copied from thread messages, 30 agents, some restricted.

    nice -n 10 .venv/bin/python bench/perf-read-routes.py

Env: TASKS (18600), OPEN (1150), AUTOS (160), NOTES (600), RUNS (11), JSON=<file> (every answer, to compare two
runs: COMPARE=a.json,b.json), EXPLAIN=1 (the plans of the cards' and the archive's statements), STATEMENTS=1.

Per route and caller it prints the statements one call takes and the median wall time of RUNS calls. Ids and times
are seeded the same each run, so two runs' answers compare as they are. Only fictional data, in a temporary folder
removed afterwards. Never point it at a real hub.
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
from datetime import datetime, timedelta, timezone
from pathlib import Path

if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    import conftest  # noqa: F401  (the fictional registry and isolated benchmark settings)

from backend import task_engine as E
from backend.auth import Identity
from backend.store import H
from backend.tests.helpers import api as T
from backend.tests.helpers import engine as X
from backend.tests.helpers.api import headers

TASKS = int(os.environ.get("TASKS", 18600))
OPEN = int(os.environ.get("OPEN", 1150))
AUTOS = int(os.environ.get("AUTOS", 160))
NOTES = int(os.environ.get("NOTES", 600))
RUNS = int(os.environ.get("RUNS", 11))
BOTS = [f"agent{n:02d}" for n in range(30)]
CALLERS = ("ana-test", "cara-test", "bot-test")
DAY = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)


def at(minutes):
    return (DAY - timedelta(minutes=minutes)).strftime("%Y-%m-%dT%H:%M:%S.000000Z")


def seed(api):
    from backend import threads
    from backend.tests.helpers.automation_api import make
    from backend.tests.helpers.threads_delivery import new_thread, say

    store = api.app.state.store
    store.settings.test_identities["bot-test"] = Identity("bot:" + BOTS[7], "bot", agent="hermes")
    rnd = random.Random(19)
    with store.transaction() as c:
        for n, slug in enumerate(BOTS):
            X.bot(c, slug, computer=False)
            if n == 7:  # bot-test's agent credential
                c.execute(
                    "INSERT INTO agents(bot,harness,token_hash,created_at,created_by,last_seen_at) "
                    "VALUES(?,'hermes','h',?,'human:ana',?)",
                    (slug, H.now(), H.now()),
                )
            boss = "human:ana" if n < 5 else "bot:" + BOTS[n - 5]
            c.execute(
                "UPDATE bot_config SET reports_to=?, owner_id=? WHERE bot=?", (boss, ("ana", "cara")[n % 2], slug)
            )
            if n % 6 == 2:
                T.restrict(
                    c, slug, see={"people": ["ana", "cara"]}, read={"people": ["ana"]}, write={"people": ["ana"]}
                )
        rows = []
        for n in range(TASKS):
            state = "open" if n < OPEN else ("done", "done", "done", "closed")[n % 4]
            made = at(rnd.randrange(0, 525600))
            done = at(rnd.randrange(0, 525600)) if state == "done" else None
            closed = at(rnd.randrange(0, 525600)) if state == "closed" else None
            # A few finished tasks with no finish time, so the archive's COALESCE reaches its later columns.
            if state != "open" and n % 97 == 0:
                done = closed = None
            owner = "bot:" + BOTS[n % len(BOTS)] if n % 5 else ("human:ana", "human:cara")[n % 2]
            private = 1 if n % 7 == 0 else 0
            rows.append(
                (
                    f"task{n:06d}",
                    f"Acme item {n}",
                    "Make the thing better. " * 6,
                    ("human:ana", "human:cara")[n % 2],
                    owner,
                    made,
                    max(x for x in (made, done or "", closed or "")),
                    n + 1,
                    private,
                    state,
                    done,
                    closed,
                )
            )
        # A task starts open (a trigger holds it); the finished ones move straight after, in one statement each.
        c.executemany(
            "INSERT INTO tasks(id,title,body,requester_id,owner_id,status,created_at,updated_at,number,private) "
            "VALUES(?,?,?,?,?,'open',?,?,?,?)",
            [row[:9] for row in rows],
        )
        for row in rows:
            if row[9] != "open":
                E._write(c, row[0], status=row[9])
        c.executemany(
            "UPDATE tasks SET done_at=?, closed_at=?, updated_at=? WHERE id=?",
            [(row[10], row[11], row[6], row[0]) for row in rows if row[9] != "open"],
        )
    made = make(api)  # one through the API, the rest copied from it with their own owners and times
    owners = ["person:ana", "person:cara", *("bot:" + b for b in BOTS[:10])]
    with store.transaction() as c:
        for n in range(AUTOS):
            aid = f"auto{n:05d}"
            c.execute(
                "INSERT INTO automations(id,name,owner_id,group_id,trigger_type,enabled,builtin,flow,version,"
                "created_at,created_by_id,updated_at,next_due_at) SELECT ?,?,?,group_id,trigger_type,enabled,builtin,"
                "json_set(json_set(flow,'$.owner',?),'$.name',?),version,?,created_by_id,?,next_due_at "
                "FROM automations WHERE id=?",
                (
                    aid,
                    f"Check {n}",
                    owners[n % len(owners)],
                    owners[n % len(owners)],
                    f"Check {n}",
                    at(n * 7),
                    at(n * 7),
                    made["id"],
                ),
            )
            for k in range(n % 4):
                c.execute(
                    "INSERT INTO automation_runs(id,automation_id,version,status,started_at,finished_at) VALUES(?,?,1,?,?,?)",
                    (f"run{n:05d}{k}", aid, "ok", at(n * 7 - k), at(n * 7 - k)),
                )
        c.execute("DELETE FROM automations WHERE id=?", (made["id"],))
    sources = []
    for k in range(6):
        thread = new_thread(api, members=("human:cara", "bot:" + BOTS[k]), title=f"Plan {k}")
        sources += [(say(api, thread["id"], f"Item {k}.{i} for the agent")["id"], BOTS[k]) for i in range(4)]
    with store.transaction() as c:
        for n in range(NOTES):
            if n % 3 == 0:
                mid, bot = sources[n % len(sources)]
                threads._defer_note(c, H.message(c, mid), "bot:" + bot)
                nid = c.execute("SELECT id FROM notes ORDER BY rowid DESC LIMIT 1").fetchone()[0]
                c.execute("UPDATE notes SET id=?, created_at=? WHERE id=?", (f"note{n:05d}", at(n), nid))
                continue
            sender = ("bot:" + BOTS[n % len(BOTS)]) if n % 4 else "human:cara"
            to = "bot:" + BOTS[(n + 3) % len(BOTS)]
            c.execute(
                "INSERT INTO notes(id,from_actor,to_actor,body,created_at) VALUES(?,?,?,?,?)",
                (f"note{n:05d}", sender, to, f"Remember item {n}", at(n)),
            )


def paths(api):
    """The reads, with each later page's start read from the page before it."""
    out = [
        "/api/v2/automations",
        "/api/v2/automations?limit=20&cursor=40",
        "/api/v2/automations?cursor=9223372036854775808",
        "/api/v2/automations?limit=500",
        "/api/v2/notes",
        "/api/v2/notes?limit=500",
        "/api/v2/places/dashboard",
        "/api/v2/tasks?status=done&sort=finished&snoozed=hide&brief=1&limit=200",
        "/api/v2/tasks?status=closed&sort=finished&snoozed=hide&brief=1&limit=200",
        "/api/v2/tasks?status=done&sort=finished&lane=company&limit=50",
    ]
    # The Done archive's next page, and one far down it (the 40th), each from the page before.
    page = api.get(out[7], headers=headers()).json()
    out.append(out[7] + "&after=" + page["next_after"])
    for _ in range(39):
        page = api.get(out[7] + "&after=" + page["next_after"], headers=headers()).json()
    out.append(out[7] + "&after=" + page["next_after"])
    return out


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


def without_timing(answer):
    """A place's answer less its per-card build times, which differ every call."""
    if isinstance(answer, dict):
        return {k: without_timing(v) for k, v in answer.items() if k not in ("ms", "took_ms", "server_timing")}
    if isinstance(answer, list):
        return [without_timing(v) for v in answer]
    return answer


def report(api):
    with api.app.state.store.read() as c:
        print({t: c.execute(f"SELECT count(*) FROM {t}").fetchone()[0] for t in ("tasks", "automations", "notes")})
    answers = {}
    for path in paths(api):
        for token in CALLERS:
            r, seen, ms = measure(api, path, token)
            body = r.json()
            answers[f"{token} {path}"] = [r.status_code, without_timing(body)]
            print(f"{path[:72]:72s} {token:9s} {r.status_code} {len(seen):5d} statements {ms:8.1f} ms")
            if os.environ.get("STATEMENTS"):
                for q, k in collections.Counter(shape(q) for q in seen).most_common(6):
                    if k > 2:
                        print(f"      {k:5d}  {q}")
    if os.environ.get("EXPLAIN"):
        explain(api)
    if os.environ.get("JSON"):
        Path(os.environ["JSON"]).write_text(json.dumps(answers, indent=1, sort_keys=True, default=str))


def explain(api):
    """The plans and median times of the Dashboard cards' recent-done counts and of the Done archive's page read
    (H.tasks, as the list route calls it, its tags included), as the company's person and as a person who is not."""
    from backend import cards

    store, auth = api.app.state.store, api.app.state.auth

    def timed(run, *args, **kwargs):
        times = []
        for _ in range(RUNS):
            t = time.perf_counter()
            run(*args, **kwargs)
            times.append((time.perf_counter() - t) * 1000)
        return statistics.median(times)

    for token in ("ana-test", "cara-test"):
        who = store.settings.test_identities[token]
        with store.read_transaction() as c:
            visible = auth.task_sql(c, who)
            ctx = cards.Ctx(c, auth, who, store.settings, None, None, None)
            for name, card, s in (
                ("tasks_by_status", cards.tasks_by_status, {"team": None}),
                ("done_per_week", cards.done_per_week, {"team": None, "weeks": 8}),
            ):
                seen = []
                c.set_trace_callback(seen.append)
                card(ctx, s)
                c.set_trace_callback(None)
                sql = next(q for q in seen if "done_at" in q)
                plan = " / ".join(row[3] for row in c.execute("EXPLAIN QUERY PLAN " + sql))
                print(f"{token} {name}: {timed(card, ctx, s):.2f} ms | {plan[:300]}")
            # A page 8,000 rows down the Done archive, by its keyset start.
            deep = c.execute(
                "SELECT COALESCE(done_at,closed_at,updated_at,created_at,''), id FROM tasks WHERE status='done' "
                "ORDER BY 1 DESC, 2 DESC LIMIT 1 OFFSET 8000"
            ).fetchone()
            for status, after in (("done", None), ("closed", None), ("done", list(deep))):
                page = dict(status=status, order="finished", limit=201, visible=visible, snoozed="hide", after=after)
                seen = []
                c.set_trace_callback(seen.append)
                rows = H.tasks(c, **page)
                c.set_trace_callback(None)
                sql = next(q for q in seen if "ORDER BY COALESCE(done_at" in q)
                plan = " / ".join(row[3] for row in c.execute("EXPLAIN QUERY PLAN " + sql))
                print(
                    f"{token} finished {status}{' deep' if after else ''} page ({len(rows)} rows): {timed(H.tasks, c, **page):.2f} ms | {plan[:300]}"
                )


def compare(a, b):
    left, right = (json.loads(Path(f).read_text()) for f in (a, b))
    for key in sorted(set(left) | set(right)):
        same = left.get(key) == right.get(key)
        print("same     " if same else "DIFFERENT", key)
        if not same:
            one, two = (json.dumps(side.get(key), sort_keys=True) for side in (left, right))
            at_ = next((i for i, (x, y) in enumerate(zip(one, two, strict=False)) if x != y), min(len(one), len(two)))
            print(
                "    before:", one[max(0, at_ - 120) : at_ + 120], "\n    after: ", two[max(0, at_ - 120) : at_ + 120]
            )


def main():
    if os.environ.get("COMPARE"):
        return compare(*os.environ["COMPARE"].split(","))
    import tempfile

    from backend.tests.helpers.api_app import api_client

    with tempfile.TemporaryDirectory(prefix=".tmp-read-routes-", dir=Path(__file__).resolve().parents[1]) as directory:
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
