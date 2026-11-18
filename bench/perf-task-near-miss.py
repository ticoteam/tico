"""Measures Auth.resolve_task's near-miss search (a mistyped 36-character task id) before and after the SQL prefilter
(backend/auth.py near_task_sql) and the banded distance, on 50,000 and 200,000 fictional tasks with random UUIDs.

    python3 -m pytest bench/perf-task-near-miss.py -n 0 -s -q

The tasks go straight into a migrated hub database (the `api` fixture): company tasks of two people and a bot, and
private tasks of other people. For the owner, a plain member and a bot it prints EXPLAIN QUERY PLAN of the old and new
statements once, then per size, caller and typed id (a one-character typo of a task the caller sees, and an id near
nothing) the median wall time of the old resolver (backend/tests/helpers/task_near_miss.py) and today's, and checks
that both give the same answer. Synthetic data: it shows the slope, not production timings.
"""

import random
import statistics
import time

from backend.auth import near_task_sql
from backend.tests.helpers.api import setup_attempt
from backend.tests.helpers.task_near_miss import edited, old_resolve_task, outcome, random_ids, seed

SIZES = (50_000, 200_000)
OLD_RUNS, NEW_RUNS = 3, 15


def median_ms(fn, runs):
    walls = []
    for _ in range(runs):
        start = time.perf_counter()
        fn()
        walls.append(time.perf_counter() - start)
    return statistics.median(walls) * 1000


def plans(store, auth, who, typed):
    with store.read() as c:
        visible = auth.task_sql(c, who)
        close, params = near_task_sql(typed)
        statements = {
            "old": (
                "SELECT id,title,owner_id,requester_id FROM tasks WHERE length(id) BETWEEN ? AND ? AND ("
                + visible
                + ")",
                (34, 38),
            ),
            "new": (
                "SELECT id,title,owner_id,requester_id FROM tasks WHERE length(id) BETWEEN ? AND ? AND "
                + close
                + " AND ("
                + visible
                + ")",
                (34, 38, *params),
            ),
        }
        for name, (sql, args) in statements.items():
            print(f"EXPLAIN QUERY PLAN ({name}):")
            for row in c.execute("EXPLAIN QUERY PLAN " + sql, args):
                print("   ", "  " * (row[1] > 0), row[3])


def test_near_miss_time_by_task_count(api):
    store, auth = api.app.state.store, api.app.state.auth
    _, _, attempt = setup_attempt(api)
    tokens = {"owner": "ana-test", "member": "eve-test", "bot": attempt["token"]}
    who = {
        name: auth.authenticate({"authorization": "Bearer " + token}, "/api/v2/tasks/x/comments", "POST")
        for name, token in tokens.items()
    }
    company = ["human:ana", "human:ben", who["bot"].actor]
    private = ["human:cara", "human:fay"]
    rng = random.Random(1)
    seeded = 0
    shown = []
    printed = False
    for size in SIZES:
        ids = random_ids(size - seeded, rng)
        cut = len(ids) * 3 // 4
        with store.transaction() as c:
            seed(c, ids[:cut], owners=company)
            seed(c, ids[cut:], owners=private, private=1)
        shown += ids[:cut]
        seeded = size
        typed = {
            "typo": edited(rng.choice(shown), [("sub", 17)], rng),
            "no-match": random_ids(1, rng)[0],
        }
        if not printed:
            plans(store, auth, who["bot"], typed["typo"])
            printed = True
        for caller, identity in who.items():
            for label, ident in typed.items():

                def new(identity=identity, ident=ident):
                    with store.read() as c:
                        return outcome(auth.resolve_task, c, identity, ident)

                def old(identity=identity, ident=ident):
                    with store.read() as c:
                        return outcome(old_resolve_task, auth, c, identity, ident)

                answer = new()
                assert answer == old(), (caller, label)
                before, after = median_ms(old, OLD_RUNS), median_ms(new, NEW_RUNS)
                print(
                    f"tasks={size:<7} {caller:<6} {label:<8} old_ms={before:9.1f} new_ms={after:8.2f} "
                    f"x{before / after:7.0f}  {answer[0]}",
                    flush=True,
                )
