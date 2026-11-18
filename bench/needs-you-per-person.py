"""Measures one person's Needs you (waiting_on.needs) as the company's open tasks grow, N = 100 and 1,500, on a
fictional company: twenty bots owned by Ana work most tasks, Ana is asked to OK some, and Ben has the same six items at
every N (five OKs named to him, and one Paused bot he owns holding up one task; his four tasks of his own are in Tasks,
not Needs you). Ana is the company's person: every task's ladder ends with her, and one in ten tasks asks her OK.
`test_needs_you_on_the_hosted_companys_shape` measures the route on the shape it was measured on live (`seed_tidy`).

    .venv/bin/python -m pytest bench/needs-you-per-person.py -n 0 -s -q

Per N it prints, for the old path (every person's lists, then Ben's: `needs_by_person(c)[ben]`) and the new one
(`needs(c, ben)`), the median wall time of RUNS reads, each in its own read snapshot, and the SQL statements one read
takes, for Ben and for Ana; and the median of the whole route, GET /api/v2/needs-you as Ben, with each read. Both paths must give each the same list; the bench fails if they differ, or if a read
it relies on scans a table instead of an index.
"""

import re
import statistics
import time

import pytest

from backend import task_engine as E
from backend.tests.helpers.api import headers
from backend import waiting_on as W
from backend.tests.helpers import engine as X

ANA, BEN = X.ANA, "human:ben"
NS = (100, 1500)
RUNS = 15
BOTS = [f"worker{n:02d}" for n in range(20)]
STAGES = (
    {"name": "Build", "owner": "assignee", "requirements": [{"kind": "owner_done"}]},
    {"name": "Review", "owner": "assignee", "requirements": [{"kind": "ok", "person": ANA, "line": "OK the build"}]},
)


def seed(store, n):
    with store.transaction() as c:
        for slug in [*BOTS, "manager", "bens-bot"]:
            X.bot(c, slug)
        c.execute("UPDATE bot_config SET owner_id='ben' WHERE bot='bens-bot'")
        X.pipeline(c, "eng", "Engineering", STAGES, manager="bot:manager")
        for k in range(4):
            X.new_task(c, ANA, f"Ben's own task {k}", "eng", assignee=BEN)
        for k in range(5):
            row = X.new_task(c, ANA, f"Needs Ben's OK {k}", "eng", assignee=f"bot:{BOTS[k]}")
            E.add_requirement(
                c,
                row["id"],
                f"bot:{BOTS[k]}",
                "ok",
                BEN,
                f"OK draft {k}",
                seen_version=row["version"],
                shows=X.shown(c, bot=BOTS[k]),
            )
        X.new_task(c, ANA, "Held up by Ben's bot", "eng", assignee="bot:bens-bot")
        c.execute("UPDATE bots SET state='paused' WHERE id='bens-bot'")
    filler = n - 10
    for start in range(0, filler, 250):
        with store.transaction() as c:
            for k in range(start, min(filler, start + 250)):
                row = X.new_task(c, ANA, f"Company task {k}", "eng", assignee=f"bot:{BOTS[k % len(BOTS)]}")
                if k % 10 == 0:
                    shows = X.shown(c, bot=row["owner_id"])
                    E.add_requirement(
                        c, row["id"], row["owner_id"], "ok", ANA, f"OK {k}", seen_version=row["version"], shows=shows
                    )


# The shape of the hosted company the route was measured on (TIDY, Nov 18: 834 statements a build for its company's
# person): about 165 bots, 1,500 open tasks, and in Ana's list some 150 pages to OK and 85 bot questions besides her
# OKs. Fictional rows only.
TIDY_BOTS = [f"bot{n:03d}" for n in range(165)]
TIDY_PAGES = 150
TIDY_QUESTIONS = 85
SPEC = (
    {
        "name": "Spec",
        "owner": "assignee",
        "requirements": [{"kind": "page", "line": "Spec page", "person": ANA, "who_oks": "none"}],
    },
)


def seed_tidy(store, n=1500):
    """`seed(store, n)`, then the hosted company's shape: TIDY_BOTS more bots, TIDY_PAGES tasks with a page attached
    for Ana to OK, and TIDY_QUESTIONS bot ❓ to Ana, on open tasks and in direct chats."""
    from backend import question_marks as Q
    from backend import task_pages as P
    from backend.store import H
    from backend.tests.helpers.page_requirement import _page, _say

    seed(store, n)
    with store.transaction() as c:
        # One in three on no computer (a cloud bot whose machine has not come up): "can this bot run" reads more of it.
        for k, slug in enumerate([*TIDY_BOTS, "writer"]):
            X.bot(c, slug, computer=k % 3 != 1)
        X.pipeline(c, "product", "Product", SPEC, manager="bot:manager")
    for start in range(0, TIDY_PAGES, 50):
        with store.transaction() as c:
            for k in range(start, min(TIDY_PAGES, start + 50)):
                row = X.new_task(c, ANA, f"Spec {k}", "product", assignee="bot:writer")
                fid, _ = _page(c, store, f"<h1>Spec {k}</h1>")
                made = P.attach(c, row["id"], "Spec page", "page:" + fid, "bot:writer", seen_version=row["version"])
                _say(c, made, "met")
    with store.transaction() as c:
        tasks = [
            r[0] for r in c.execute("SELECT id, owner_id FROM tasks WHERE title LIKE 'Company task %' ORDER BY rowid")
        ]
        owners = dict(c.execute("SELECT id, owner_id FROM tasks WHERE title LIKE 'Company task %'").fetchall())
        # About half on a task's chat, half in a bot's direct chat with Ana (one open ❓ per bot per chat).
        on_tasks = TIDY_QUESTIONS // 2
        for k, task_id in enumerate(tasks[:on_tasks]):
            said = H.task_comment(c, owners[task_id], task_id, f"Should note {k} go out on Friday?", wake=False)
            Q.mark(c, owners[task_id], said["id"], ANA)
        for k in range(TIDY_QUESTIONS - on_tasks):
            Q.ask_in_chat(c, f"bot:{TIDY_BOTS[k]}", ANA, f"Which tone should note {k} take?")


def timed(store, read):
    statements: list[str] = []
    times = []
    out = None
    for run in range(RUNS):
        with store.read_transaction() as c:
            if run == 0:
                c.set_trace_callback(statements.append)
            start = time.perf_counter()
            out = read(c)
            times.append(time.perf_counter() - start)
            c.set_trace_callback(None)
    return statistics.median(times) * 1000, len(statements), out


def route(api, token, runs=RUNS):
    """The median wall time of GET /api/v2/needs-you as `token`, and its items."""
    times, out = [], None
    for _ in range(runs):
        start = time.perf_counter()
        response = api.get("/api/v2/needs-you", headers=headers(token))
        times.append(time.perf_counter() - start)
        assert response.status_code == 200, response.text
        out = response.json()["items"]
    return statistics.median(times) * 1000, out


@pytest.mark.parametrize("n", NS)
def test_needs_you_for_one_person(api, n, monkeypatch):
    company = api.app.state.store
    seed(company, n)
    with company.read_transaction() as c:
        assert (
            c.execute("SELECT count(*) FROM tasks WHERE status='open' AND pipeline_id IS NOT NULL").fetchone()[0] >= n
        )
    old_ms, old_sql, old = timed(company, lambda c: W.needs_by_person(c).get(BEN, []))
    new_ms, new_sql, new = timed(company, lambda c: W.needs(c, BEN))
    print(f"\nN={n:5d} open tasks, Ben has {len(new)} items")
    print(f"  old needs_by_person[ben]  {old_ms:8.2f} ms median  {old_sql:5d} statements")
    print(f"  new needs(ben)            {new_ms:8.2f} ms median  {new_sql:5d} statements")
    assert len(new) == 6
    assert new == old
    # The company's person (Ana) is on every task's ladder: her read checks a few columns of every open task, and works
    # out in full only those that may fall to her.
    old_ms, old_sql, old = timed(company, lambda c: W.needs_by_person(c).get(ANA, []))
    new_ms, new_sql, new = timed(company, lambda c: W.needs(c, ANA))
    print(f"  Ana, the company's person, has {len(new)} items")
    print(f"  old needs_by_person[ana]  {old_ms:8.2f} ms median  {old_sql:5d} statements")
    print(f"  new needs(ana)            {new_ms:8.2f} ms median  {new_sql:5d} statements")
    assert new == old
    # The whole route (its mentions and the rest of what it lists), with the old read and with the new one.
    new_ms, new_items = route(api, "ben-test")
    with monkeypatch.context() as patched:
        patched.setattr(W, "needs", lambda c, person, *_, **__: W.needs_by_person(c).get(person, []))
        old_ms, old_items = route(api, "ben-test")
    print(f"  GET /api/v2/needs-you as Ben: old {old_ms:8.2f} ms, new {new_ms:8.2f} ms median")
    assert new_items == old_items
    with company.read_transaction() as c:
        plans = [
            [str(step[3]) for step in c.execute("EXPLAIN QUERY PLAN " + sql, args).fetchall()]
            for sql, args in (
                (W._OPEN_SQL % "t.id", ()),
                ("SELECT id FROM visits WHERE person_id=? AND left_at IS NULL", (BEN,)),
                (
                    "SELECT visit_id FROM answers WHERE answer='give' AND notes IN (SELECT value FROM json_each(?))",
                    ("[]",),
                ),
            )
        ]
    for plan in plans:
        print("  plan:", " / ".join(plan))
        assert all("USING" in step for step in plan if step.startswith("SCAN") and "json_each" not in step)


def _counted(monkeypatch, store):
    """Every SQL statement the store's connections run from here on, in a list."""
    seen: list[str] = []
    take, give = store._take, store._give

    def counting_take():
        c = take()
        c.set_trace_callback(seen.append)
        return c

    def quiet_give(c):
        c.set_trace_callback(None)
        give(c)

    monkeypatch.setattr(store, "_take", counting_take)
    monkeypatch.setattr(store, "_give", quiet_give)
    return seen


def test_needs_you_on_the_hosted_companys_shape(api, monkeypatch):
    """The route as the company's person on the hosted company's shape (`seed_tidy`): the SQL statements and median
    time of the whole list, of a walk through its pages (limit=50 and each `next`), and of the badge's count."""
    from backend.blobs import Blobs

    store = api.app.state.store
    store.blobs = store.blobs or Blobs(store.settings)
    seed_tidy(store)
    walks = []

    def walk():
        pages, url = 0, "/api/v2/needs-you?limit=50"
        while url:
            response = api.get(url, headers=headers("ana-test"))
            assert response.status_code == 200, response.text
            pages += 1
            after = response.json()["next"]
            url = f"/api/v2/needs-you?limit=50&cursor={after}" if after else None
        walks.append(pages)

    for label, read in (
        ("whole list", lambda: route(api, "ana-test", runs=1)),
        ("page walk", walk),
        ("count=1", lambda: api.get("/api/v2/needs-you?count=1", headers=headers("ana-test"))),
    ):
        read()  # warm
        times, counts = [], []
        for _ in range(RUNS):
            with monkeypatch.context() as patched:
                seen = _counted(patched, store)
                start = time.perf_counter()
                read()
                times.append(time.perf_counter() - start)
                counts.append(len(seen))
        print(
            f"\n  Ana {label:10s} {statistics.median(times) * 1000:8.1f} ms median "
            f"{statistics.median(counts):6.0f} statements"
            + (f" over {walks[-1]} pages" if label == "page walk" else "")
        )
    whole = api.get("/api/v2/needs-you", headers=headers("ana-test")).json()
    print(f"  Ana has {whole['count']} items: {whole['by_kind']}")
    # The query plan of every batched read (one statement for many rows: `IN (SELECT value FROM json_each(?))`) the
    # whole list ran, as it ran it: each must look its rows up by an index, not scan a table.
    with monkeypatch.context() as patched:
        seen = _counted(patched, store)
        route(api, "ana-test", runs=1)
    batched = {}
    for sql in seen:
        if "json_each" in sql:
            batched.setdefault(re.sub(r"'\[[^']*\]'", "'[...]'", sql), sql)
    with store.read() as c:
        for shape, sql in sorted(batched.items()):
            plan = [row[3] for row in c.execute("EXPLAIN QUERY PLAN " + sql)]
            print(f"\n  EXPLAIN QUERY PLAN {shape}")
            for line in plan:
                print(f"    {line}")
            scans = [line for line in plan if line.startswith("SCAN ") and "json_each" not in line]
            assert not scans, (shape, plan)
