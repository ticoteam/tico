"""Measures what live streams viewing one thread each are sent of bot run traffic (Perf I48, Measure 1), on a fictional
company: THREADS threads, each with one bot running in it, and STREAMS browser streams, each following one thread with
`thread=` and the `threads` topic, as the thread view does.

    python3 -m pytest bench/perf-run-scope.py -n 0 -s -q

The fixture writes STEPS seconds of activity: one output step per running bot per second (through attempt_events, so
the triggers log the `runs` rows) and one `bot_status` row per root every 5 s, the triggers' throttle. Every stream is
then served from one shared snapshot, as a round of streams is (events.Rounds). Per stream it prints the run events
sent, the bytes of every event sent, and the thread_bot_status.status calls made for it; then the totals, and how many
of a stream's run events came from a thread other than the one it views.
"""

import json
import statistics

from backend import bot_settings as BS
from backend import events as E
from backend import thread_bot_status
from backend.store import H
from backend.tests.helpers.api import assign, claim, post, ready, runner

THREADS, STREAMS, STEPS, STATUS_EVERY = 20, 50, 60, 5
BOTS = [f"bot{n:02d}" for n in range(THREADS)]


def fixture(api):
    """THREADS threads, each with a run of its own bot claimed; returns the thread ids and the attempt ids."""
    with api.app.state.store.transaction() as c:
        H.sync_registry(
            c,
            {slug: {"name": slug, "runtime": "fake", "status": "active"} for slug in BOTS},
            {"people": [{"id": p, "email": p + "@acme.example"} for p in ("ana", "ben", "cara")]},
        )
        for slug in BOTS:
            c.execute("INSERT INTO bot_config(bot,owner_id) VALUES(?,?)", (slug, "ana"))
            BS.write(c, slug, {"name": slug, "runtime": "fake"})
    threads, attempts = [], []
    for n, slug in enumerate(BOTS):
        machine = runner(api)  # one computer per bot: a computer runs only so many at once
        assign(api, machine, slug)
        ready(api, machine, [slug])
        thread = post(
            api,
            "threads",
            {"title": f"Busy {n:02d}", "audience": "members", "members": ["human:ben", "bot:" + slug]},
        )
        post(api, f"threads/{thread['id']}/messages", {"text": f"@{slug} please work on it"})
        attempt = claim(api, machine, slug)
        assert attempt, slug
        threads.append(thread["id"])
        attempts.append(attempt["id"])
    return threads, attempts


def activity(api, threads, attempts):
    """STEPS seconds of run output in every thread and a bot_status row per root every STATUS_EVERY seconds."""
    with api.app.state.store.transaction() as c:
        for second in range(STEPS):
            for thread, attempt in zip(threads, attempts, strict=True):
                c.execute(
                    "INSERT INTO attempt_events(attempt_id,seq,kind,payload,created_at) VALUES(?,?,?,?,?)",
                    (attempt, 10_000 + second, "status", json.dumps({"text": f"step {second}"}), H.now()),
                )
                if second % STATUS_EVERY == 0:
                    c.execute(
                        "INSERT INTO changes(topic,kind,subject_id,conversation_id,created_at) "
                        "VALUES('threads','bot_status',?,?,?)",
                        (thread, thread, H.now()),
                    )


def test_run_traffic_per_viewing_stream(api, monkeypatch):
    threads, attempts = fixture(api)
    store, auth = api.app.state.store, api.app.state.auth
    ben = auth.settings.test_identities["ben-test"]
    with store.read() as c:
        start = E.latest(c)
    activity(api, threads, attempts)

    calls = []
    real = thread_bot_status.status
    monkeypatch.setattr(thread_bot_status, "status", lambda c, v, row: calls.append(row["id"]) or real(c, v, row))
    views = [threads[n % THREADS] for n in range(STREAMS)]
    report = []
    with store.read() as c:
        snapshot = E._Snapshot(c, [E._Poll(None, None, start, (view,)) for view in views])
        for n, view in enumerate(views):
            E._between_viewers(c)
            calls.clear()
            after, runs_sent, foreign, sent_bytes = start, 0, 0, 0
            while True:
                events, after, reset, more = E.read_in(
                    c,
                    snapshot,
                    auth,
                    ben,
                    after,
                    topics=("runs", "threads"),
                    threads_scope=(view,),
                    task_views=lambda rows, c, who, visible_sql="1": [],
                    task_view=None,
                )
                assert not reset
                for _, topic, data in events:
                    sent_bytes += len(json.dumps(dict(data), default=str))
                    if topic == "runs":
                        runs_sent += 1
                        foreign += data["conversation_id"] != view
                if not more:
                    break
            report.append((n, runs_sent, foreign, sent_bytes, len(calls), len(set(calls))))
    print()
    print(f"{THREADS} threads, {STREAMS} streams viewing one thread each, {STEPS} s of steps")
    print(f"{'stream':>6} {'runs':>6} {'foreign':>8} {'bytes':>9} {'status':>7} {'roots':>6}")
    for n, runs_sent, foreign, sent_bytes, status_calls, roots in report:
        print(f"{n:>6} {runs_sent:>6} {foreign:>8} {sent_bytes:>9} {status_calls:>7} {roots:>6}")
    totals = [sum(row[i] for row in report) for i in range(1, 6)]
    print(
        f"total  runs={totals[0]} foreign={totals[1]} bytes={totals[2]} status_calls={totals[3]} "
        f"median_runs_per_stream={statistics.median(r[1] for r in report)} "
        f"median_status_calls_per_stream={statistics.median(r[4] for r in report)}"
    )
