"""Measures POST /api/v2/jobs/claim (the work path and the idle path) on a fictional company, with K assigned bots on
one computer, K = 5, 20 and 50.

    python3 -m pytest bench/perf-core-2.py -n 0 -s -q

Per K and computer kind it prints the statements one work claim takes (reads and writes, from the store's connections),
the median wall time of RUNS claims, and a digest of the claims' answers in order (an equal digest means the same bots
were handed out in the same order). Each work claim has a queued chat from every bot: the claim's per-bot checks run for
all K. After a claim the computer completes that run and its bot gets a new chat, so each claim does the same work.
The idle claim is a computer whose K bots have nothing queued. "older" is a computer that does not advertise
browser_exit_v1, so the claim reads each bot's browser exit; "current" advertises it, as the runner does.
"""
import contextlib
import hashlib
import statistics
import time

from backend import bot_settings as BS
from backend.store import H
from backend.tests import test_api
from backend.tests.test_api import assign, headers, post, ready, runner

api = test_api.api                                   # the fixture, bound so that tests can take it
KS = (5, 20, 50)
RUNS = 50
CURRENT = ["task_lanes_v2", "session_roots_v1", "task_goals_v1", "capacity_continue_v1", "source_continue_v1",
           "turns_at_once_v1", "private_lanes_v1", "browser_exit_v1", "threads_v1"]
OLDER = [cap for cap in CURRENT if cap != "browser_exit_v1"]


def seed(api, prefix, count, capabilities, queued):
    store = api.app.state.store
    slugs = [f"{prefix}-{n:02d}" for n in range(count)]
    with store.transaction() as c:
        H.sync_registry(c, {s: {"name": s, "runtime": "fake", "status": "active"} for s in slugs},
                        {"people": [{"id": p, "email": p + "@acme.example"} for p in ("ana", "ben", "cara")]})
        for s in slugs:
            c.execute("INSERT INTO bot_config(bot,owner_id) VALUES(?,'ana')", (s,))
            BS.write(c, s, {"name": s, "runtime": "fake"})
    machine = runner(api, label=f"Bench {prefix}")
    for s in slugs:
        assign(api, machine, s)
    ready(api, machine, slugs, capabilities)
    if queued:
        for s in slugs:
            chat(api, s)
    return machine, slugs


def chat(api, slug):
    return post(api, "chat/" + slug, {"text": "Please summarize the current work."})


@contextlib.contextmanager
def traced(api):
    """Every statement the request runs, on any connection the store lends it (reads, and the write transaction too)."""
    store, seen = api.app.state.store, []
    original = store.read

    @contextlib.contextmanager
    def borrowed():
        with original() as c:
            c.set_trace_callback(seen.append)
            try:
                yield c
            finally:
                c.set_trace_callback(None)
    store.read = borrowed
    try:
        yield seen
    finally:
        del store.read


def claim(api, machine, cold=False):
    """POST jobs/claim as the computer, timed; (answer, statements, seconds)."""
    store = api.app.state.store
    if cold:
        store.roster_rows.clear()
    with traced(api) as seen:
        start = time.perf_counter()
        response = api.post("/api/v2/jobs/claim", json={"bot": None, "start_places": True},
                            headers=headers(machine["token"]))
        wall = time.perf_counter() - start
    assert response.status_code == 200, response.text
    return response.json(), len(seen), wall


def finish(api, machine, answer):
    """The run the claim handed out completes and its bot is asked again, outside the timed call."""
    attempt = answer.get("attempt") or {}
    if not attempt:
        return None
    aid = attempt["id"]
    post(api, f"attempts/{aid}/started", {"thread_id": "t-" + aid}, machine["token"])
    post(api, f"attempts/{aid}/complete", {"outcome": "completed", "last_seq": 0, "text": "Done."}, machine["token"])
    return attempt.get("bot_id") or attempt.get("bot")


def measure(api, label, machine, slugs, runs):
    walls, counts, handed, empty = [], [], [], []
    for _ in range(runs):
        answer, statements, wall = claim(api, machine)
        walls.append(wall)
        counts.append(statements)
        handed.append(finish(api, machine, answer) or "none")
        if handed[-1] == "none":
            empty.append(answer)
        else:
            chat(api, handed[-1])
    digest = hashlib.sha1(" ".join(handed).encode()).hexdigest()[:12]
    print(f"{label:<26} statements={min(counts)}..{max(counts)} median_ms={statistics.median(walls) * 1000:8.2f} "
          f"handed={runs - len(empty):<3} digest={digest}", flush=True)
    if empty:
        print(f"  {len(empty)} claims handed nothing; first answer: {str(empty[0])[:300]}", flush=True)
    return counts


def test_claim_statements_and_time_by_assigned_bots(api):
    for kind, capabilities in (("current", CURRENT), ("older", OLDER)):
        for k in KS:
            machine, slugs = seed(api, f"{kind[0]}{k}", k, capabilities, queued=True)
            answer, statements, _ = claim(api, machine, cold=True)
            assert answer.get("attempt"), answer            # the work path hands out a run, not an idle answer
            chat(api, finish(api, machine, answer))
            measure(api, f"work {kind} K={k}", machine, slugs, RUNS)
            idle, idle_slugs = seed(api, f"i{kind[0]}{k}", k, capabilities, queued=False)
            _, idle_statements, _ = claim(api, idle, cold=True)
            idle_walls = [claim(api, idle)[2] for _ in range(RUNS)]
            print(f"{'idle ' + kind + ' K=' + str(k):<26} statements={idle_statements:<5} "
                  f"median_ms={statistics.median(idle_walls) * 1000:8.2f}", flush=True)
