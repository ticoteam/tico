"""Measures a round of people's live streams (backend/events.py Rounds) catching up together on perf-events-setup's
fictional company at the hosted TIDY company's size, with a bot's run writing STEPS output steps at the end of the
log: 10 streams each of Ana and Ben and 5 of Cara, as the web app opens them (all topics, `output=compact`), opened at
once from BACK changes before the run's first step. It prints how long until every stream had caught up, the time the
server spent serving their rounds (the bench's own clients share its process, so that is the server's work), and the
SQL statements that took.

    nice -n 10 .venv/bin/python bench/perf-events-rounds.py

Env as bench/perf-events-setup.py (OPEN, CLOSED, THREADS, DB, SEED_ONLY, FRAMES, COMPARE), plus STEPS (default 200)
and BACK (default 100). FRAMES holds every stream's text, to compare two checkouts' on one DB. Only fictional data.
Never point it at a real hub.
"""

import importlib.util
import json
import os
import statistics
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
_spec = importlib.util.spec_from_file_location("events_setup", Path(__file__).with_name("perf-events-setup.py"))
S = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(S)
B = S.B

from backend import events as E
from backend.store import H

STEPS = int(os.environ.get("STEPS", 200))
BACK = int(os.environ.get("BACK", 100))
PEOPLE = (("ana-test", 10), ("ben-test", 10), ("cara-test", 5))
_seed = B.seed


def seed(api):
    from backend.tests.helpers.api import setup_attempt

    _seed(api)
    _, _, attempt = setup_attempt(api)
    with api.app.state.store.transaction() as c:
        for n in range(STEPS):
            kind, payload = (
                ("delta", {"delta_kind": "text", "text": f"Step {n} of the reply. " * 8})
                if n % 3
                else ("tool", {"name": "Read", "input": {"path": f"docs/{n}.md"}, "output": "x" * 1500})
            )
            c.execute(
                "INSERT INTO attempt_events(attempt_id,seq,kind,payload,created_at) VALUES(?,?,?,?,?)",
                (attempt["id"], 1000 + n, kind, json.dumps(payload), H.now()),
            )


def report(api):
    if os.environ.get("SEED_ONLY"):
        return
    store = api.app.state.store
    with store.read() as c:
        newest = E.latest(c)
        # From BACK changes before the seeded run's first step: the app's own writes as it opens come after it.
        first = c.execute("SELECT min(seq) FROM changes WHERE topic='runs' AND kind='output'").fetchone()[0]
        after = (first or newest) - BACK
        kinds = c.execute(
            "SELECT topic, count(*) FROM changes WHERE seq>? GROUP BY topic ORDER BY 2 DESC", (after,)
        ).fetchall()
    print("newest change", newest, "| after", after, "by topic:", {k: n for k, n in kinds})
    query = f"?output=compact&after={after}"
    # The server's own work: every round's time on the thread that serves it (the bench's clients share the process).
    spent = []
    real_round = E.Rounds._round

    def timed(self, jobs):
        t = time.perf_counter()
        try:
            return real_round(self, jobs)
        finally:
            spent.append(time.perf_counter() - t)

    E.Rounds._round = timed
    frames = {}
    with S._serving(api.app) as base:
        for wave in range(3):
            probes = [(token, S.Probe(base, token, query)) for token, n in PEOPLE for _ in range(n)]
            seen, gate, began = [], threading.Barrier(len(probes)), [0.0]

            def go(probe, gate=gate, began=began):
                gate.wait()
                if not began[0]:
                    began[0] = time.perf_counter()
                probe.run(began[0])

            spent.clear()
            with B.counting(store, seen):
                threads = [threading.Thread(target=go, args=(p,)) for _, p in probes]
                for t in threads:
                    t.start()
                for t in threads:
                    t.join()
            for _, p in probes:
                assert p.error is None, p.error
            done = [p.done_at * 1000 for _, p in probes]
            print(
                f"wave {wave}: {len(probes)} streams caught up: median {statistics.median(done):8.1f} ms "
                f"max {max(done):8.1f} ms | rounds {len(spent)}, {sum(spent) * 1000:8.1f} ms serving them | "
                f"{len(seen)} statements | "
                f"{sum(len(p.text) for _, p in probes) / 1024:.0f} KB sent"
            )
            if wave == 0:
                counts = {}
                for token, p in probes:
                    counts[token] = counts.get(token, 0) + 1
                    frames[f"{token} #{counts[token]}"] = p.text
            time.sleep(S.QUIET)
    for token, _ in PEOPLE:
        mine = {text for key, text in frames.items() if key.startswith(token)}
        print(f"{token}: its {len([k for k in frames if k.startswith(token)])} streams sent {len(mine)} distinct texts")
    if os.environ.get("FRAMES"):
        Path(os.environ["FRAMES"]).write_text(json.dumps(frames, indent=1, sort_keys=True))


B.seed = seed
B.report = report

if __name__ == "__main__":
    if os.environ.get("COMPARE"):
        S.compare(*os.environ["COMPARE"].split(","))
    else:
        B.main()
