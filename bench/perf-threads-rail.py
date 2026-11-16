"""Measures the Threads rail's first page (GET /api/v2/threads/rail) for a reader with 5, 50 and 500 unread bot replies
in a bot's Home, each reply from its own run that read three messages (P09).

    python3 -m pytest bench/perf-threads-rail.py -n 0 -s -q

Per size it prints, cold (after a provenance change, as after any new message: production's usual case) and warm, the\nstatements one call runs and the median wall time of RUNS calls, with the per-message check's priming on and off (off:\neach reply's run read one at a time, as before P09), on the same machine and data.
Synthetic fixtures only: it shows the slope, not production timings.
"""

import collections
import re
import statistics
import time

from backend import thread_rail
from backend.tests.slug_ids import bot_ids_are_their_slugs  # noqa: F401  (each bot's id is its slug)
from backend.tests.helpers.api import get, headers
from backend.tests.helpers.task_privacy_reads import traced
from backend.tests.helpers.thread_rail_batches import runner, seed_replies  # noqa: F401  (fixture)

SIZES = (5, 50, 500)
RUNS = 7
PATH = "threads/rail"


def touch(api):
    """Any new message moves the provenance clock, so the next load works out each run again: production's usual case."""
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE provenance_clock SET value=value+1 WHERE id=1")


def median_ms(api, cold):
    walls = []
    for _ in range(RUNS):
        if cold:
            touch(api)
        start = time.perf_counter()
        get(api, PATH, "ben-test")
        walls.append(time.perf_counter() - start)
    return statistics.median(walls) * 1000


def test_rail_statements_and_time_by_bot_replies(api, monkeypatch):
    home = api.get("/api/v2/threads/home/bot:ops", headers=headers("ben-test")).json()
    primed = thread_rail._prime
    done, primed_sql = 0, {}
    for size in SIZES:
        seed_replies(api, home["id"], size - done, f"b{size}")
        done = size
        for cold in (True, False):
            row = {}
            for label, prime in (("before", lambda c, rows: None), ("after", primed)):
                monkeypatch.setattr(thread_rail, "_prime", prime)
                get(api, PATH, "ben-test")
                if cold:
                    touch(api)
                _, statements = traced(api, lambda: get(api, PATH, "ben-test"))
                row[label] = len(statements), median_ms(api, cold)
                top = collections.Counter(re.sub(r"'[^']*'|\\b\\d+\\b", "?", q)[:70] for q in statements)
                row[label + "_top"] = top.most_common(1)
            (bs, bms), (as_, ams) = row["before"], row["after"]
            primed_sql[size, cold] = as_
            print(
                f"replies={size:<4} {'cold' if cold else 'warm'}  unprimed: {bs:>5} statements {bms:8.1f} ms   "
                f"primed: {as_:>5} statements {ams:8.1f} ms   x{bms / ams:.1f}   most repeated primed: "
                f"{row['after_top']}",
                flush=True,
            )
    for cold in (True, False):  # AC-1: 500 replies within the 5-reply count plus 15
        assert primed_sql[500, cold] - primed_sql[5, cold] <= 15, primed_sql
