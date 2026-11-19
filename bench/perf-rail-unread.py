"""Measures the thread rail and Catch up for a reader with thousands of deliveries, on a fictional company the size of
the hosted TIDY one (Nov 19: 19k conversations, 40k messages, one person with 6.8k deliveries and 17k follows; flight
recorder: the `WITH unread` statement 258 ms per slow rail call, 4.8 times a call). It seeds what bench/perf-hub-overload.py
does, then Ana's DMs with bots full of bot replies delivered to her (DM_POSTS each), half of them read part way, follows
of FOLLOWS finished-task rooms, some bot posts restricted or under a legacy audience, some deliveries with a stale or
missing thread and some messages moved to another thread after delivery.

    nice -n 10 .venv/bin/python bench/perf-rail-unread.py

Env: DM_POSTS (110), FOLLOWS (6500), RUNS (5), DB=<file> (keep the seeded database there and reuse it),
ANSWERS=<file> (each route's answer as JSON, to compare two checkouts' on one DB), TOKENS (ana-test,ben-test,cara-test),
PATHS (/api/v2/threads/rail,/api/v2/threads/catch-up), PLANS=1 (each `WITH unread` statement's plan).

Per route and reader it prints the statements one call takes, the median wall time of RUNS calls, and for the
`WITH unread` statements their number, their time and their SQLite VM steps when rerun alone. Only fictional data, in a
temporary folder removed afterwards (unless DB is given). Never point it at a real hub.
"""

import contextlib
import importlib.util
import json
import os
import sqlite3
import statistics
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
if __name__ == "__main__":
    sys.path.insert(0, str(HERE.parent))
    import conftest  # noqa: F401  (the fictional registry and isolated benchmark settings)
_spec = importlib.util.spec_from_file_location("perf_hub_overload", HERE / "perf-hub-overload.py")
overload = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(overload)  # the company it seeds

DM_POSTS = int(os.environ.get("DM_POSTS", 110))
FOLLOWS = int(os.environ.get("FOLLOWS", 6500))
RUNS = int(os.environ.get("RUNS", 5))
TOKENS = tuple(os.environ.get("TOKENS", "ana-test,ben-test,cara-test").split(","))
PATHS = tuple(os.environ.get("PATHS", "/api/v2/threads/rail,/api/v2/threads/catch-up").split(","))


def seed(api):
    from backend import threads
    from backend.store import H

    overload.seed(api)
    store = api.app.state.store
    with store.read() as c:
        dms = [
            r[0]
            for r in c.execute(
                "SELECT c.id FROM conversations c JOIN thread_followers f ON f.thread_id=c.id AND f.actor='human:ana' "
                "WHERE c.parent_id IS NULL AND c.task_id IS NULL AND COALESCE(c.subject,'')='' ORDER BY c.rowid"
            )
        ]
        bots = {
            r[0]: r[1]
            for r in c.execute(
                "SELECT f.thread_id, f.actor FROM thread_followers f WHERE f.actor LIKE 'bot:%' AND f.thread_id IN "
                "(SELECT value FROM json_each(?))",
                (json.dumps(dms),),
            )
        }
    print(f"{len(dms)} DMs of Ana, {len(bots)} with a bot")
    for n, dm in enumerate(dms):
        bot = bots.get(dm)
        if not bot:
            continue
        with store.transaction() as c:
            for k in range(DM_POSTS):
                threads.post(c, bot, dm, f"Reply {k} from {bot}")
            if n % 2 == 0:  # read part way: a mark in the middle of the bot's replies
                seq = c.execute(
                    "SELECT seq FROM messages WHERE conversation_id=? ORDER BY seq LIMIT 1 OFFSET ?",
                    (dm, DM_POSTS // 2),
                ).fetchone()[0]
                c.execute(
                    "UPDATE thread_followers SET last_read_seq=? WHERE thread_id=? AND actor='human:ana'", (seq, dm)
                )
    with store.transaction() as c:
        rooms = [
            r[0]
            for r in c.execute(
                "SELECT id FROM conversations WHERE parent_id IS NULL AND task_id IS NOT NULL ORDER BY rowid LIMIT ?",
                (FOLLOWS,),
            )
        ]
        now = H.now()
        c.executemany(
            "INSERT OR IGNORE INTO thread_followers(id,thread_id,actor,reason,created_at,updated_at) "
            "VALUES(?,?,'human:ana','manual',?,?)",
            [(H.new_id(), room, now, now) for room in rooms],
        )
        # Some bot posts only some readers read; a few under a legacy audience.
        c.execute(
            "UPDATE messages SET restricted=1 WHERE sender_id LIKE 'bot:%' AND rowid % 7 = 0 AND conversation_id IN "
            "(SELECT value FROM json_each(?))",
            (json.dumps(dms),),
        )
        c.execute(
            "UPDATE messages SET legacy_audience='[\"bot:bot000\"]' WHERE sender_id LIKE 'bot:%' AND rowid % 29 = 0 "
            "AND conversation_id IN (SELECT value FROM json_each(?))",
            (json.dumps(dms),),
        )
        # A delivery's copy of its thread is not what places it: some stale, some missing, some messages moved.
        c.execute("UPDATE message_deliveries SET thread_id=NULL WHERE rowid % 11 = 0")
        c.execute("UPDATE message_deliveries SET thread_id=root_id WHERE rowid % 13 = 0")
        for n in range(0, len(dms) - 1, 6):
            dm, other = dms[n], dms[n + 1]
            c.execute(
                "UPDATE messages SET conversation_id=? WHERE id IN (SELECT id FROM messages WHERE conversation_id=? "
                "AND sender_id LIKE 'bot:%' ORDER BY seq DESC LIMIT 3)",
                (other, dm),
            )


@contextlib.contextmanager
def tracing(store, seen):
    originals = {name: getattr(store, name) for name in ("read", "read_transaction")}

    def traced(opened):
        @contextlib.contextmanager
        def borrowed():
            with opened() as c:
                c.set_trace_callback(lambda q: seen.append((time.perf_counter(), q)))
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


def steps(db, sql):
    """(median ms of 3 runs, SQLite VM steps, rows) of one statement run alone."""
    count = [0]

    def tick():
        count[0] += 1
        return 0

    times = []
    for _ in range(3):
        t = time.perf_counter()
        n = len(db.execute(sql).fetchall())
        times.append((time.perf_counter() - t) * 1000)
    db.set_progress_handler(tick, 1)
    try:
        db.execute(sql).fetchall()
    finally:
        db.set_progress_handler(None, 1)
    return statistics.median(times), count[0], n


def report(api, path_db):
    from backend.tests.helpers.api import headers

    store, answers = api.app.state.store, {}
    with store.read() as c:
        print(
            {
                t: c.execute(f"SELECT count(*) FROM {t}").fetchone()[0]
                for t in ("conversations", "messages", "message_deliveries", "thread_followers")
            },
            "ana's deliveries:",
            c.execute("SELECT count(*) FROM message_deliveries WHERE recipient='human:ana'").fetchone()[0],
            "follows:",
            c.execute("SELECT count(*) FROM thread_followers WHERE actor='human:ana'").fetchone()[0],
        )
    db = sqlite3.connect(Path(path_db).as_uri() + "?mode=ro", uri=True)
    for path in PATHS:
        for token in TOKENS:
            r = api.get(path, headers=headers(token))
            assert r.status_code == 200, r.text[:300]
            answer = r.json()
            if isinstance(answer, dict):
                answer.pop("seq", None)  # the change number the page is as of, not part of its rows
            answers[f"{path} {token}"] = answer
            seen = []
            with tracing(store, seen):
                api.get(path, headers=headers(token))
            seen.append((time.perf_counter(), ""))
            unread = [
                (b[0] - a[0], a[1]) for a, b in zip(seen[:-1], seen[1:], strict=True) if a[1].startswith("WITH unread")
            ]
            times = []
            for _ in range(RUNS):
                t = time.perf_counter()
                api.get(path, headers=headers(token))
                times.append((time.perf_counter() - t) * 1000)
            alone = [steps(db, q) for _, q in unread]
            print(
                f"{path:26s} {token:9s} {len(seen) - 1:5d} statements {statistics.median(times):8.1f} ms | "
                f"WITH unread x{len(unread)}: {sum(ms for ms, _, _ in alone):7.1f} ms alone, "
                f"{sum(s for _, s, _ in alone):9d} VM steps, {sum(n for _, _, n in alone)} rows"
            )
            if os.environ.get("PLANS") and unread:
                for row in db.execute("EXPLAIN QUERY PLAN " + unread[0][1]):
                    print("     ", row[3])
    if os.environ.get("ANSWERS"):
        Path(os.environ["ANSWERS"]).write_text(json.dumps(answers, sort_keys=True, default=str))


def main():
    import shutil
    import tempfile

    from backend.tests.helpers.api_app import api_client

    keep = os.environ.get("DB")
    with tempfile.TemporaryDirectory(prefix=".tmp-rail-", dir=HERE.parent) as directory:
        folder = Path(directory)
        reuse = keep and Path(keep).exists()
        install = overload.reopened(folder, keep) if reuse else api_client(folder, None)
        api = next(install)
        try:
            if not reuse:
                t = time.perf_counter()
                seed(api)
                print(f"seeded in {time.perf_counter() - t:.1f}s")
            saved = keep or str(folder / "seeded.sqlite")
            if not reuse:
                with api.app.state.store.read() as c:
                    c.execute("VACUUM INTO ?", (saved,))
            report(api, saved)
        finally:
            install.close()
            shutil.rmtree(folder, ignore_errors=True)


if __name__ == "__main__":
    main()
