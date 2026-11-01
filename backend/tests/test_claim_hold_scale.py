"""Claim write-lock holds at a reported install's scale (TK-INSRN53V): 102 bots, 12 computers, 291 routines.

Synthetic rows only. Twelve computers claim at once round after round while heartbeats run and a few leases lapse
each round, as they do when renewals queue behind the lock. Prints a receipt of each claim phase under the lock,
how often the claim's own expiry sent it to the full selection, and every write hold the claim route took."""
import json
import math
import random
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest

from backend import auto_tasks, execution, flight, org_turns
from backend.store import H, encode, _pooled
from backend.tests.test_api import api, assign, headers, post, runner  # noqa: F401

BOTS, COMPUTERS, ROUTINES, ROUNDS, LAPSES = 102, 12, 291, 30, 3


def seed(api):
    store = api.app.state.store
    slugs = [f"crew-{i:03d}" for i in range(BOTS)]
    with store.transaction() as c:
        H.sync_registry(c, {s: {"name": s, "runtime": "fake", "status": "active"} for s in slugs}, [])
        for s in slugs:
            c.execute("INSERT INTO bot_config(bot,config,operator) VALUES(?,?,'ana')",
                      (s, encode({"name": s, "runtime": "fake"})))
    computers = [runner(api, label=f"Synthetic {n}") for n in range(COMPUTERS)]
    places = {}
    for i, s in enumerate(slugs):
        computer = computers[i % COMPUTERS]
        assign(api, computer, s)
        places.setdefault(computer["runner_id"], []).append(s)
    for computer in computers:
        post(api, "runners/heartbeat", {"version": "test", "platform": "test",
             "readiness": {s: True for s in places[computer["runner_id"]]}}, token=computer["token"])
    now = H.now()
    with store.transaction() as c:
        for n in range(ROUTINES):
            c.execute("INSERT INTO schedules(id,bot,routine_key,cron,event_name,title,playbook,next_due,source,updated_at) "
                      "VALUES(?,?,?,?,NULL,?,'',?,'synthetic',?)",
                      (f"routine-{n}", slugs[n % BOTS], f"routine-{n}", "0 9 * * *", f"Routine {n}",
                       H.shift(now, hours=6), now))
        # Work for every bot: a chat each and three tasks each, as a busy install's queue holds.
        for s in slugs:
            room = f"chat-{s}"
            c.execute("INSERT INTO conversations(id,kind,participants,created_at) VALUES(?,'chat',?,?)",
                      (room, encode(["human:ana", "bot:" + s]), now))
            for k in range(3):
                c.execute("INSERT INTO messages(id,conversation_id,from_actor,to_actor,kind,body,created_at,refs) "
                          "VALUES(?,?,'human:ana',?,'say','Synthetic work',?,'{}')",
                          (f"work-{s}-{k}", room, "bot:" + s, now))
        # Room for many turns on each computer, and up long enough to be trusted with a lease.
        c.execute("UPDATE runners SET capacity=40,awake_since=?,last_seen_at=?", (H.shift(now, minutes=-10), now))
    return computers, places


class Phases:
    """Time spent in each claim phase while `claim` holds the write lock (one thread per claim)."""

    def __init__(self):
        self.local, self.lock = threading.local(), threading.Lock()
        self.times, self.fallbacks, self.full_under_lock, self.claims = {}, 0, 0, []

    def add(self, name, ms):
        with self.lock:
            self.times.setdefault(name, []).append(ms)

    def wrap(self, monkeypatch, owner, name, label=None):
        original = getattr(owner, name)
        phases = self

        def timed(*a, **k):
            if not getattr(phases.local, "inside", False):
                return original(*a, **k)
            start = time.perf_counter()
            conn = next((x for x in a if hasattr(x, "total_changes")), None)
            before = conn.total_changes if conn is not None else None
            try:
                return original(*a, **k)
            finally:
                phases.add(label or name, (time.perf_counter() - start) * 1000)
                if name == "expire" and conn is not None and conn.total_changes != before:
                    with phases.lock:
                        phases.fallbacks += 1      # the claim's own expiry changed rows: the old code chose afresh
                if name == "auto_tasks.missing" or label == "auto_tasks.missing":
                    phases.local.backfill = True       # the backfill's own selection is not a repeat
                elif name == "candidate" and k.get("job_id") is None and not getattr(phases.local, "backfill", False):
                    with phases.lock:
                        phases.full_under_lock += 1
        monkeypatch.setattr(owner, name, timed)

    def around_claim(self, monkeypatch, cls):
        original = cls.claim
        phases = self

        def claim(self_, c, *a, **k):
            phases.local.inside, phases.local.backfill = True, False
            start = time.perf_counter()
            try:
                return original(self_, c, *a, **k)
            finally:
                phases.local.inside = False
                with phases.lock:
                    phases.claims.append((time.perf_counter() - start) * 1000)
        monkeypatch.setattr(cls, "claim", claim)


def pct(values, q):
    return round(sorted(values)[max(0, math.ceil(len(values) * q) - 1)], 2) if values else 0.0


@pytest.mark.slow
def test_claim_write_hold_at_102_bots(api, monkeypatch):
    computers, places = seed(api)
    store, ex = api.app.state.store, api.app.state.execution
    rec = flight.Recorder()
    store.recorder, store.factory = rec, flight.connection_class(rec)
    store.pooled = _pooled(store.factory)
    with store.pool_lock:
        for c in store.pool:
            c.close()
        store.pool.clear()
    monkeypatch.setattr(flight, "LONG_HOLD_MS", 0)
    holds = []
    monkeypatch.setattr(rec, "long_hold", lambda route, site, hold_ms, wait_ms: holds.append(
        {"route": route, "site": site, "hold_ms": hold_ms, "wait_ms": wait_ms}))
    phases = Phases()
    cls = type(ex)
    phases.around_claim(monkeypatch, cls)
    for name in ("expire", "candidate", "held_starts", "company_waiter"):
        phases.wrap(monkeypatch, cls, name)
    phases.wrap(monkeypatch, auto_tasks, "missing", "auto_tasks.missing")
    phases.wrap(monkeypatch, org_turns, "observe", "org_turns.observe")
    errors, taken = [], 0
    rng = random.Random(678)

    def routed(path, call):
        # The app names a hold's route only when it starts with a recorder; name it here as the server would.
        _, token = flight.request_started("POST", {"route": SimpleNamespace(path=path)})
        try:
            return call()
        finally:
            flight.request_finished(token)

    def claim(computer):
        r = routed("/api/v2/jobs/claim", lambda: api.post(
            "/api/v2/jobs/claim", headers=headers(computer["token"]), json={"start_places": True}))
        if r.status_code != 200:
            errors.append((r.status_code, r.text[:200]))
            return None
        return r.json().get("attempt")

    def beat(computer):
        r = routed("/api/v2/runners/heartbeat", lambda: api.post(
            "/api/v2/runners/heartbeat", headers=headers(computer["token"]), json={
                "version": "test", "platform": "test", "capacity": 40,
                "readiness": {s: True for s in places[computer["runner_id"]]}}))
        if r.status_code != 200:
            errors.append((r.status_code, r.text[:200]))

    started = time.monotonic()
    with ThreadPoolExecutor(max_workers=2 * COMPUTERS) as pool:
        for _ in range(ROUNDS):
            # Renewals that queued behind the lock: a few leases lapse on the server's side each round.
            with store.transaction() as c:
                live = [r[0] for r in c.execute("SELECT id FROM attempts WHERE state='leased'")]
                lapsed = rng.sample(live, min(LAPSES, len(live)))
                c.executemany("UPDATE attempts SET lease_until=? WHERE id=?",
                              [(H.shift(H.now(), seconds=-1), aid) for aid in lapsed])
            work = [pool.submit(claim, computer) for computer in computers]
            work += [pool.submit(beat, computer) for computer in computers]
            for pending in work[:COMPUTERS]:
                taken += bool(pending.result(timeout=120))
            for pending in work[COMPUTERS:]:
                pending.result(timeout=120)
    seconds = time.monotonic() - started
    claim_holds = [h for h in holds if h["route"] == "POST /api/v2/jobs/claim"]
    beats = [h["hold_ms"] for h in holds if h["route"] == "POST /api/v2/runners/heartbeat"]
    by_site = {}
    for h in claim_holds:
        by_site.setdefault(h["site"], []).append(h["hold_ms"])
    receipt = {
        "bots": BOTS, "computers": COMPUTERS, "routines": ROUTINES, "rounds": ROUNDS, "lapses_per_round": LAPSES,
        "seconds": round(seconds, 1), "claims": len(phases.claims), "attempts_taken": taken,
        "fallbacks": phases.fallbacks, "full_selection_under_lock": phases.full_under_lock,
        "claim_under_lock_ms": {"p50": pct(phases.claims, .5), "p95": pct(phases.claims, .95),
                                "max": pct(phases.claims, 1)},
        "phases_ms": {name: {"n": len(v), "p95": pct(v, .95), "max": pct(v, 1), "total": round(sum(v), 1)}
                      for name, v in sorted(phases.times.items())},
        "claim_route_holds_ms": {site: {"n": len(v), "p95": pct(v, .95), "max": pct(v, 1)}
                                 for site, v in sorted(by_site.items())},
        "heartbeat_holds_ms": {"n": len(beats), "p95": pct(beats, .95), "max": pct(beats, 1)},
        "all_claim_holds_ms": {"n": len(claim_holds), "p95": pct([h["hold_ms"] for h in claim_holds], .95),
                               "max": pct([h["hold_ms"] for h in claim_holds], 1)},
    }
    print("CLAIM_HOLD_RECEIPT " + json.dumps(receipt, indent=1), flush=True)
    assert not errors, errors[:5]
    assert taken, "the synthetic queue handed out no work"
    assert claim_holds and receipt["all_claim_holds_ms"]["max"] < 5000, receipt
    # A claim chosen on the read snapshot never repeats the full selection under the lock because its own expiry
    # requeued work: the route expires lapsed leases in its own short write first.
    assert phases.fallbacks == 0 and phases.full_under_lock == 0, receipt
