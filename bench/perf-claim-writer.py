"""Measures what a runner's claim does while it holds the single SQLite writer (POST /api/v2/jobs/claim, the recheck of
the job it selected, execution.candidate with `job_id`), on a fictional company shaped like the hosted TIDY company
(Nov 19: 13 computers, 171 bots, open holds on bots, plans given to most computers, a Finance book).

    nice -n 10 .venv/bin/python bench/perf-claim-writer.py

Env: RUNNERS (10), BOTS (17 per computer), HOLDS (300 open holds on other computers' bots), INPUTS (100 inputs of
50 KB for the run-messages read), RUNS (20).

Per claim that hands out a run: the write-lock hold (BEGIN IMMEDIATE to COMMIT), the statements inside it, and how
many of them read every open hold or every computer's grants. Then the EXPLAIN and timing of the reads this change
replaces. Only fictional data, in a temporary folder removed afterwards. Never point it at a real hub.
"""

import collections
import dataclasses
import os
import shutil
import statistics
import sys
import tempfile
import threading
import time
from pathlib import Path

if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    import conftest  # noqa: F401  (the fictional registry and isolated benchmark settings)

from backend import flight
from backend.store import H

RUNNERS = int(os.environ.get("RUNNERS", 10))
BOTS = int(os.environ.get("BOTS", 17))
HOLDS = int(os.environ.get("HOLDS", 300))
INPUTS = int(os.environ.get("INPUTS", 100))
RUNS = int(os.environ.get("RUNS", 20))
WHOLE = {
    "every open hold": lambda sql: sql.strip() == "SELECT * FROM holds INDEXED BY holds_bot WHERE released_at IS NULL",
    "every computer's grants": lambda sql: (
        "FROM computer_subscriptions g LEFT JOIN model_subscriptions s" in sql
        and "g.runner_id IN" not in sql
        and not sql.startswith("SELECT 1 ")
    ),
    "the whole Finance chart": lambda sql: "SELECT * FROM fin_accounts WHERE book_id=? ORDER BY code" in sql,
}
OLD_HOLDS = "SELECT * FROM holds INDEXED BY holds_bot WHERE released_at IS NULL"
OLD_CHART = "SELECT * FROM fin_accounts WHERE book_id=? ORDER BY code"


class Meter:
    """Write-lock holds: (ms, statements inside) while `on`, and the statements' texts."""

    def __init__(self, recorder):
        self.on, self.local, self.holds, self.texts = False, threading.local(), [], []
        query, begin = recorder.query, flight.begin

        def counted(sql, ms, locked=False):
            if self.on and getattr(self.local, "held", None) is not None:
                self.local.held[1] += 1
                self.texts.append(sql)
            return query(sql, ms, locked)

        def timed(conn, rec=None, **kwargs):
            done = begin(conn, rec, **kwargs)
            self.local.held = [time.perf_counter(), 0]

            def finished():
                started, statements = self.local.held
                self.local.held = None
                if self.on:
                    self.holds.append(((time.perf_counter() - started) * 1000, statements))
                return done()

            return finished

        recorder.query, flight.begin = counted, timed


def seed(api):
    from backend import holds
    from backend.tests.helpers.claim_statements import seed as computer

    store = api.app.state.store
    machines = [computer(api, f"c{n}", BOTS) for n in range(RUNNERS)]
    now = H.now()
    with store.transaction() as c:
        bots = [r[0] for r in c.execute("SELECT id FROM bots WHERE id NOT LIKE 'c0-%' ORDER BY id")]
        for n in range(HOLDS):  # open holds on other computers' bots (and a few on none)
            bot = bots[n % len(bots)]
            holds.put(
                c, holds.CLAIM_ERROR, "job", f"job-{n}", actor=H.TICO_ACTOR, bot_id=bot, until_at=H.shift(now, hours=1)
            )
        for sid, kind in (("plan-claude", "claude_token"), ("plan-codex", "codex_login")):
            c.execute(
                "INSERT OR IGNORE INTO model_subscriptions(id,name,kind,sealed,status,added_by,created_at,updated_at) "
                "VALUES(?,?,?,'','working','human:ana',?,?)",
                (sid, sid, kind, now, now),
            )
        for m in machines[1:]:  # every other computer is given both plans
            for runtime, sid in (("claude", "plan-claude"), ("codex", "plan-codex")):
                c.execute(
                    "INSERT OR REPLACE INTO computer_subscriptions(runner_id,runtime,subscription_id,set_by,updated_at) "
                    "VALUES(?,?,?,'human:ana',?)",
                    (m["runner_id"], runtime, sid, now),
                )
    return machines[0]


def handed_run(api, machine, answer):
    from backend.tests.helpers.api import post

    aid = answer["attempt"]["id"]
    post(api, f"attempts/{aid}/started", {"thread_id": "t-" + aid}, machine["token"])
    post(api, f"attempts/{aid}/complete", {"outcome": "completed", "last_seq": 0, "text": "Done."}, machine["token"])
    bot = answer["attempt"].get("bot_id") or answer["attempt"].get("bot")
    post(api, "chat/" + bot, {"text": "Next, please."})


def claims(api, machine, meter):
    from backend.tests.helpers.api import headers

    rows, whole, last = [], collections.Counter(), None
    for _ in range(RUNS):
        meter.holds, meter.texts, meter.on = [], [], True
        r = api.post("/api/v2/jobs/claim", json={"bot": None, "start_places": True}, headers=headers(machine["token"]))
        meter.on = False
        assert r.status_code == 200, r.text[:300]
        answer = r.json()
        if not answer.get("attempt"):
            continue
        rows.append((sum(h[0] for h in meter.holds), sum(h[1] for h in meter.holds)))
        for label, text in WHOLE.items():
            whole[label] += sum(1 for sql in meter.texts if text(sql))
        handed_run(api, machine, answer)
        last = answer["attempt"]["id"]
    ms, st = [r[0] for r in rows], [r[1] for r in rows]
    print(
        f"claims handing out a run: {len(rows)} | write-lock hold p50 {statistics.median(ms):6.1f} ms max "
        f"{max(ms):6.1f} ms | statements in lock p50 {statistics.median(st):5.0f}"
    )
    for label in WHOLE:
        print(f"  reads of {label} in the lock, per claim: {whole[label] / max(1, len(rows)):.1f}")
    return last


def explain(api, aid):
    """The reads replaced: plans and timings on this database."""
    new_messages = (  # backend/live_privacy.py ATTEMPT_MESSAGES_SQL, written out so the bench also runs before it
        "SELECT m.* FROM messages m WHERE m.id IN (SELECT j.message_id FROM attempts a JOIN jobs j ON j.id=a.job_id "
        "WHERE a.id=? UNION SELECT i.message_id FROM attempt_inputs i WHERE i.attempt_id=?) ORDER BY m.id"
    )
    store = api.app.state.store
    now = H.now()
    with store.transaction() as c:
        c.execute(
            "INSERT INTO conversations(id,kind,participants,created_at) VALUES('bench-room','chat','[]',?)", (now,)
        )
        for n in range(INPUTS):
            c.execute(
                "INSERT INTO messages(id,conversation_id,sender_id,recipient_id,kind,body,created_at,refs) "
                "VALUES(?,'bench-room','human:ana','bot:c0-00','say',?,?,'{}')",
                (f"bench-in-{n}", "y" * 50_000, now),
            )
            c.execute("INSERT INTO attempt_inputs(attempt_id,message_id) VALUES(?,?)", (aid, f"bench-in-{n}"))
    old_union = (
        "SELECT m.* FROM attempts a JOIN jobs j ON j.id=a.job_id JOIN messages m ON m.id=j.message_id WHERE a.id=? "
        "UNION SELECT m.* FROM attempt_inputs i JOIN messages m ON m.id=i.message_id WHERE i.attempt_id=?"
    )
    with store.read() as c:
        book = c.execute("SELECT id FROM fin_books LIMIT 1").fetchone()
        reads = [
            ("old: every open hold", OLD_HOLDS, ()),
            (
                "new: one bot's and no bot's holds",
                "SELECT * FROM holds INDEXED BY holds_bot WHERE released_at IS NULL AND bot_id IN (?,?) UNION ALL "
                "SELECT * FROM holds INDEXED BY holds_bot WHERE released_at IS NULL AND bot_id IS NULL",
                ("c0-00", ""),
            ),
            ("old: run messages, UNION of rows", old_union, (aid, aid)),
            ("new: run messages, ids first", new_messages, (aid, aid)),
        ]
        if book:
            reads += [
                ("old: the whole Finance chart", OLD_CHART, (book[0],)),
                (
                    "new: one account by code",
                    "SELECT id FROM fin_accounts WHERE book_id=? AND code=?",
                    (book[0], "6400"),
                ),
            ]
        for label, sql, args in reads:
            plan = " | ".join(r[3] for r in c.execute("EXPLAIN QUERY PLAN " + sql, args))
            rows = len(c.execute(sql, args).fetchall())
            t = time.perf_counter()
            for _ in range(50):
                c.execute(sql, args).fetchall()
            print(f"  {label:36s} {(time.perf_counter() - t) * 1000 / 50:7.3f} ms  {rows:4d} rows  {plan}")


def main():
    from backend.tests.helpers import api_app

    plain = api_app.api_settings
    api_app.api_settings = lambda folder: dataclasses.replace(plain(folder), flight_recorder=True)
    with tempfile.TemporaryDirectory(prefix=".tmp-claim-writer-", dir=Path(__file__).resolve().parents[1]) as d:
        folder = Path(d)
        install = api_app.api_client(folder, None)
        api = next(install)
        try:
            t = time.perf_counter()
            machine = seed(api)
            print(
                f"seeded in {time.perf_counter() - t:.1f}s: {RUNNERS} computers, {RUNNERS * BOTS} bots, {HOLDS} holds"
            )
            meter = Meter(api.app.state.store.recorder)
            explain(api, claims(api, machine, meter))
        finally:
            install.close()
            shutil.rmtree(folder, ignore_errors=True)


if __name__ == "__main__":
    main()
