"""Measures a Review stage gate's read of second reviews' messages (task_requirements._named_commits) on a fictional
company the size of the hosted TIDY one (Nov 19): CONVERSATIONS conversations, MESSAGES messages (most of them said),
REVIEWS second reviews each in a conversation of three messages. Prints the old and new statement's query plan, the
median time of the eleven reads one Needs-you build runs, and whether both return the same rows.

    nice -n 19 .venv/bin/python bench/perf-review-commits.py

Only fictional data, in a temporary folder removed afterwards. Never point it at a real hub.
"""

import json
import os
import random
import shutil
import sqlite3
import statistics
import sys
import tempfile
import time
from pathlib import Path

if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend import task_requirements as R
from backend.config import Settings
from backend.hubdb import MESSAGE_TASK_SQL
from backend.store import Store

CONVERSATIONS = int(os.environ.get("CONVERSATIONS", 19000))
MESSAGES = int(os.environ.get("MESSAGES", 39000))
REVIEWS = int(os.environ.get("REVIEWS", 83))
READS = 11  # second reviews one Needs-you build reads on TIDY
RUNS = int(os.environ.get("RUNS", 21))
NEW = R._review_messages_sql(MESSAGE_TASK_SQL)
# The statement as it was before (v0.6, Nov 19), kept apart from NEW so a change to both still shows.
OLD = (
    "SELECT m.body FROM messages m LEFT JOIN conversations cv ON cv.id = m.conversation_id "
    "WHERE m.conversation_id=? AND m.kind='say' AND m.deleted_at IS NULL AND m.superseded_at IS NULL "
    f"AND {MESSAGE_TASK_SQL}=? ORDER BY m.created_at, m.id"
)


def seed(c):
    rnd = random.Random(7)
    start = 1_763_000_000
    stamp = lambda n: time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(start + n)) + ".000000Z"
    c.executemany(
        "INSERT INTO conversations(id, kind, task_id, created_at, scope) VALUES(?,?,?,?,?)",
        [(f"cv{n}", "task", f"t{n}", stamp(n), "direct") for n in range(CONVERSATIONS)],
    )
    kinds = ["say"] * 8 + ["event", "ask"]
    c.executemany(
        "INSERT INTO messages(id, conversation_id, sender_id, kind, body, refs, created_at) VALUES(?,?,?,?,?,?,?)",
        [
            (
                f"m{n}",
                f"cv{rnd.randrange(REVIEWS, CONVERSATIONS)}",
                "bot:builder",
                rnd.choice(kinds),
                f"Note {n} on PR #{n % 900} at {n:07x}",
                json.dumps({"task": f"t{n % CONVERSATIONS}"}) if n % 3 else None,
                stamp(n * 7),
            )
            for n in range(MESSAGES - 3 * REVIEWS)
        ],
    )
    c.executemany(
        "INSERT INTO messages(id, conversation_id, sender_id, kind, body, refs, created_at) VALUES(?,?,?,?,?,?,?)",
        [
            (f"r{n}-{k}", f"cv{n}", "bot:reviewer", "say", f"Reviewed PR #{n} at {n:07x}{k}", None, stamp(n * 13 + k))
            for n in range(REVIEWS)
            for k in range(3)
        ],
    )


def timed(c, sql, reviews):
    times = []
    for _ in range(RUNS):
        began = time.perf_counter()
        for n in reviews:
            c.execute(sql, (f"cv{n}", f"t{n}")).fetchall()
        times.append((time.perf_counter() - began) * 1000)
    return statistics.median(times)


def main():
    folder = Path(tempfile.mkdtemp(prefix="perf-review-commits-"))
    try:
        path = folder / "hub.sqlite"
        Store(Settings(db_path=path)).initialize(seed_market=False)
        with sqlite3.connect(path) as c:
            seed(c)
        with sqlite3.connect(path) as c:
            print(
                "seeded:",
                c.execute("SELECT count(*) FROM conversations").fetchone()[0],
                "conversations,",
                c.execute("SELECT count(*) FROM messages").fetchone()[0],
                "messages; sqlite_stat1:",
                bool(c.execute("SELECT 1 FROM sqlite_master WHERE name='sqlite_stat1'").fetchone()),
            )
            reviews = random.Random(3).sample(range(REVIEWS), READS)
            for name, sql in (("old", OLD), ("new", NEW)):
                plan = [r[3] for r in c.execute("EXPLAIN QUERY PLAN " + sql, ("cv0", "t0"))]
                print(f"\n{name} plan:")
                for step in plan:
                    print("    ", step)
                print(f"{name}: {READS} reads, {timed(c, sql, reviews):.3f} ms median of {RUNS}")
            expected = {n: [(f"Reviewed PR #{n} at {n:07x}{k}",) for k in range(3)] for n in range(REVIEWS)}
            same = all(
                c.execute(sql, (f"cv{n}", f"t{n}")).fetchall() == expected[n]
                for n in range(REVIEWS)
                for sql in (OLD, NEW)
            )
            print(f"\nold and new read each review's three messages, in order: {same}")
            if not same:
                sys.exit(1)
    finally:
        shutil.rmtree(folder, ignore_errors=True)


if __name__ == "__main__":
    main()
