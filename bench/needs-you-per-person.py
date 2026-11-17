"""Measures one person's Needs you (waiting_on.needs) as the company's open tasks grow, N = 100 and 1,500, on a
fictional company: twenty bots owned by Ana work most tasks, Ana is asked to OK some, and Ben has the same ten items at
every N (four tasks of his own, five OKs named to him, and one Paused bot he owns holding up one task). Ana is the
company's person: every task's ladder ends with her, and one in ten tasks asks her OK.

    .venv/bin/python -m pytest bench/needs-you-per-person.py -n 0 -s -q

Per N it prints, for the old path (every person's lists, then Ben's: `needs_by_person(c)[ben]`) and the new one
(`needs(c, ben)`), the median wall time of RUNS reads, each in its own read snapshot, and the SQL statements one read
takes, for Ben and for Ana; and the median of the whole route, GET /api/v2/needs-you as Ben, with each read. Both paths must give each the same list; the bench fails if they differ, or if a read
it relies on scans a table instead of an index.
"""

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
            E.add_requirement(c, row["id"], f"bot:{BOTS[k]}", "ok", BEN, f"OK draft {k}", seen_version=row["version"])
        X.new_task(c, ANA, "Held up by Ben's bot", "eng", assignee="bot:bens-bot")
        c.execute("UPDATE bots SET state='paused' WHERE id='bens-bot'")
    filler = n - 10
    for start in range(0, filler, 250):
        with store.transaction() as c:
            for k in range(start, min(filler, start + 250)):
                row = X.new_task(c, ANA, f"Company task {k}", "eng", assignee=f"bot:{BOTS[k % len(BOTS)]}")
                if k % 10 == 0:
                    E.add_requirement(c, row["id"], row["owner_id"], "ok", ANA, f"OK {k}", seen_version=row["version"])


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
    assert len(new) == 10
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
        patched.setattr(W, "needs", lambda c, person: W.needs_by_person(c).get(person, []))
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
