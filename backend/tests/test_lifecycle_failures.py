"""Known failure classes of a bot turn (an "attempt"), driven over the HTTP API the runner uses: an event batch out of
order, events after the turn ended, a lease that lapses mid-turn, a late renewal, and a server restart while a turn runs.

Already covered elsewhere, so not repeated here: an identical replay, a conflicting replay and a gap in one batch are
atomic (test_attempt_contention.py::test_event_batch_replay_conflict_and_gap_are_atomic); a renewal or batch that
reached a busy server keeps its turn (test_lease_held.py); a restart gives lapsed leases one more period
(test_recovery.py::test_restart_gives_lapsed_leases_one_more_period_instead_of_expiring_them); a refused provider
sign-in neither replays a turn nor re-queues it in a loop (test_execution_review.py::
test_rejected_signin_cannot_replay_a_run_that_acted); usage limits and repeated limit failures are held and probed
once per window (test_login_holds.py, test_run_backoff.py)."""

import json
import random
from contextlib import ExitStack
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from backend.app import create_app
from backend.store import H
from backend.tests.test_api import api, assign, claim, expire, headers, post, ready, runner, setup_attempt  # noqa: F401


def started(api, attempt, token):
    post(api, f"attempts/{attempt['id']}/started", {"thread_id": "lifecycle"}, token)


def events(api, attempt, token, *seqs):
    batch = [{"seq": s, "kind": "delta", "payload": {"text": f"chunk {s}"}} for s in seqs]
    return api.post(f"/api/v2/attempts/{attempt['id']}/events", json={"events": batch}, headers=headers(token))


def attempt_row(api, aid):
    with api.app.state.store.read() as c:
        return dict(c.execute("SELECT state,last_seq,lease_until FROM attempts WHERE id=?", (aid,)).fetchone())


def job_state(api, job_id):
    with api.app.state.store.read() as c:
        return c.execute("SELECT state FROM jobs WHERE id=?", (job_id,)).fetchone()[0]


def live_attempts(api, job_id):
    with api.app.state.store.read() as c:
        return c.execute("SELECT count(*) FROM attempts WHERE job_id=? AND state IN ('leased','running')",
                         (job_id,)).fetchone()[0]


def test_events_out_of_order_within_a_batch_are_a_gap_and_nothing_lands(api):
    r, _, a = setup_attempt(api)
    started(api, a, r["token"])
    reversed_batch = events(api, a, r["token"], 2, 1)
    assert reversed_batch.status_code == 409 and reversed_batch.json()["error"]["code"] == "event_gap"
    assert attempt_row(api, a["id"])["last_seq"] == 0
    assert events(api, a, r["token"], 1, 2).json()["ack_seq"] == 2


def test_events_after_the_attempt_completed_are_refused_as_terminal_and_nothing_is_applied(api):
    r, _, a = setup_attempt(api)
    started(api, a, r["token"])
    assert events(api, a, r["token"], 1).json()["ack_seq"] == 1
    post(api, f"attempts/{a['id']}/complete", {"outcome": "completed", "last_seq": 1, "text": "Done."}, r["token"])
    late = events(api, a, r["token"], 2)
    assert late.status_code == 409 and late.json()["error"]["code"] == "terminal", late.text
    assert attempt_row(api, a["id"])["state"] == "completed" and attempt_row(api, a["id"])["last_seq"] == 1
    with api.app.state.store.read() as c:
        assert c.execute("SELECT count(*) FROM attempt_events WHERE attempt_id=?", (a["id"],)).fetchone()[0] == 1


def test_a_turn_lapsed_before_it_started_is_offered_once_and_its_late_events_do_not_touch_the_new_one(api):
    r, _, old = setup_attempt(api)
    # The runner stops renewing before it starts: the lease passes and the work goes back in line.
    expire(api, old["id"])
    ready(api, r, ["ops"])
    new = claim(api, r)
    assert new and new["job_id"] == old["job_id"]
    assert claim(api, r) is None
    assert live_attempts(api, old["job_id"]) == 1
    late = events(api, old, r["token"], 1)
    assert late.status_code == 200 and late.json()["historical"] is True, late.text
    assert attempt_row(api, old["id"])["state"] == "expired"
    assert attempt_row(api, new["id"])["last_seq"] == 0 and live_attempts(api, old["job_id"]) == 1


def test_a_turn_lapsed_mid_run_is_not_offered_to_another_computer_and_its_late_events_are_archived(api):
    r, _, a = setup_attempt(api)
    started(api, a, r["token"])
    assert events(api, a, r["token"], 1).json()["ack_seq"] == 1
    # The runner stops renewing mid-turn: the lease passes and the sweep ends the attempt.
    expire(api, a["id"])
    assert claim(api, r) is None
    assert attempt_row(api, a["id"])["state"] == "expired"
    # Its work may already have acted, so it is held for a check, and no second attempt starts on it.
    assert job_state(api, a["job_id"]) == "uncertain" and live_attempts(api, a["job_id"]) == 0
    assert claim(api, r) is None
    late = events(api, a, r["token"], 2)
    assert late.status_code == 200 and late.json()["historical"] is True, late.text
    assert attempt_row(api, a["id"])["last_seq"] == 2
    assert job_state(api, a["job_id"]) == "uncertain" and live_attempts(api, a["job_id"]) == 0


def test_a_finished_turn_whose_lease_lapsed_before_any_sweep_is_settled_not_refused(api):
    r, _, a = setup_attempt(api)
    started(api, a, r["token"])
    assert events(api, a, r["token"], 1).json()["ack_seq"] == 1
    # The lease runs out while the turn finishes, and no sweep runs before the runner hands in its result.
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE attempts SET lease_until=? WHERE id=?", (H.shift(H.now(), seconds=-5), a["id"]))
    done = api.post(f"/api/v2/attempts/{a['id']}/complete", headers=headers(r["token"]),
                    json={"outcome": "completed", "last_seq": 1, "text": "Opened the pull request."})
    assert done.status_code == 200, done.text
    assert attempt_row(api, a["id"])["state"] != "running" and live_attempts(api, a["job_id"]) == 0
    assert job_state(api, a["job_id"]) == "completed"


def test_a_late_renewal_after_the_task_went_to_another_attempt_is_refused_and_the_old_one_stays_ended(api):
    r, _, old = setup_attempt(api)
    expire(api, old["id"])
    ready(api, r, ["ops"])
    new = claim(api, r)
    assert new and new["job_id"] == old["job_id"]
    late = api.post(f"/api/v2/attempts/{old['id']}/renew", json={}, headers=headers(r["token"]))
    assert late.status_code == 409 and late.json()["error"]["code"] == "stale_lease", late.text
    assert attempt_row(api, old["id"])["state"] == "expired"
    assert attempt_row(api, new["id"])["state"] == "leased" and live_attempts(api, old["job_id"]) == 1


def test_a_server_restart_mid_turn_keeps_the_runners_events_renewals_and_completion_working(api):
    from fastapi.testclient import TestClient
    from backend.app import create_app
    r, _, a = setup_attempt(api)
    started(api, a, r["token"])
    assert events(api, a, r["token"], 1).json()["ack_seq"] == 1
    # A deploy restarts the API against the same database while the turn is running.
    with TestClient(create_app(api.app.state.store.settings)) as restarted:
        # The runner resends its last batch (it never saw the ack), then continues.
        resent = events(restarted, a, r["token"], 1, 2)
        assert resent.status_code == 200 and resent.json()["ack_seq"] == 2, resent.text
        renewed = restarted.post(f"/api/v2/attempts/{a['id']}/renew", json={}, headers=headers(r["token"]))
        assert renewed.status_code == 200 and renewed.json()["lease_until"] > H.now(), renewed.text
        done = post(restarted, f"attempts/{a['id']}/complete",
                    {"outcome": "completed", "last_seq": 2, "text": "Finished after the restart."}, r["token"])
        assert done["outcome"] == "completed"
        assert job_state(api, a["job_id"]) == "completed" and live_attempts(api, a["job_id"]) == 0
        with restarted.app.state.store.read() as c:
            assert c.execute("SELECT count(*) FROM attempt_events WHERE attempt_id=?", (a["id"],)).fetchone()[0] == 2


# A seeded random walk over the turn lifecycle: 2 computers, 3 jobs, one bot each. Every step is an HTTP call a runner
# could make (or a bad one it could make by mistake), the clock moves in 15 s steps, and sometimes the server restarts
# over the same database. After each step the invariants below must hold. A failure prints the seed and every action
# so the walk can be replayed.
STEPS = 40
SEEDS = [1, 2, 3, 4]
RESTARTS = 2                                    # building the app again costs about a third of a second
START = datetime(2026, 10, 28, 12, 0, 0, tzinfo=timezone.utc)


def send(client, method, path, token, body=None):
    """The raw answer, so a random walk can see refusals as well as successes."""
    call = getattr(client, method)
    return call("/api/v2/" + path, json=body or {}, headers=headers(token))


def db_attempts(api):
    with api.app.state.store.read() as c:
        return [dict(r) for r in c.execute("SELECT id,job_id,state,last_seq FROM attempts ORDER BY created_at,id")]


def db_events(api, aid):
    with api.app.state.store.read() as c:
        return {r["seq"]: json.loads(r["payload"])["text"] for r in
                c.execute("SELECT seq,payload FROM attempt_events WHERE attempt_id=?", (aid,))}


def check(api, model, log):
    """Every invariant, against what the database holds now. Returns a list of problems (empty when all hold)."""
    problems = []
    # The expiry sweep runs on a schedule as well as on claims; run it here so a lapsed lease is judged as the server would.
    with api.app.state.store.transaction() as c:
        api.app.state.execution.expire(c)
    attempts = db_attempts(api)
    live_by_job = {}
    for a in attempts:
        events_here = db_events(api, a["id"])
        if a["state"] in ("leased", "running"):
            live_by_job.setdefault(a["job_id"], []).append(a["id"])
        if sorted(events_here) != list(range(1, a["last_seq"] + 1)):
            problems.append(f"attempt {a['id'][:8]}: stored seqs {sorted(events_here)} do not match last_seq {a['last_seq']}")
        if a["last_seq"] < model["last_seq"].get(a["id"], 0):
            problems.append(f"attempt {a['id'][:8]}: last_seq went down")
        model["last_seq"][a["id"]] = a["last_seq"]
        for seq, text in model["acked"].get(a["id"], {}).items():
            if events_here.get(seq) != text:
                problems.append(f"attempt {a['id'][:8]}: acknowledged event {seq} is missing or changed")
        if a["state"] in ("completed", "failed"):
            frozen = model["terminal"].setdefault(a["id"], (a["state"], len(events_here)))
            if frozen != (a["state"], len(events_here)):
                problems.append(f"attempt {a['id'][:8]}: changed after {frozen[0]} ({frozen[1]} events, now "
                                f"{a['state']} with {len(events_here)})")
    for job_id, live in live_by_job.items():
        if len(live) > 1:
            problems.append(f"job {job_id[:8]}: {len(live)} live attempts at once")
    with api.app.state.store.read() as c:
        for job_id, state in c.execute("SELECT id,state FROM jobs"):
            if state == "running" and job_id not in live_by_job:
                problems.append(f"job {job_id[:8]} is running with no live attempt")
    return problems


def walk(api, time_machine, seed, rng_steps=STEPS):
    rng = random.Random(seed)
    time_machine.move_to(START.strftime("%Y-%m-%dT%H:%M:%SZ"), tick=False)
    now = START
    # Two computers: A runs ops (two jobs), B runs cpo (one job).
    a, b = runner(api, label="A"), runner(api, label="B")
    assign(api, a, "ops")
    assign(api, b, "cpo", operator="ben-test")
    post(api, "chat/ops", {"text": "First ops request."})
    post(api, "chat/ops", {"text": "Second ops request."})
    post(api, "chat/cpo", {"text": "A product request."})
    computers = {"A": (a, ["ops"]), "B": (b, ["cpo"])}
    model = {"acked": {}, "last_seq": {}, "terminal": {}, "batches": {}, "started": set()}
    log = []
    stack = ExitStack()
    client = stack.enter_context(TestClient(api.app))
    turn_ids = []                                   # attempts the walk has seen, in claim order
    owner = {}                                      # attempt id -> the computer that claimed it
    restarts = 0
    problems = check(api, model, log)
    for step in range(rng_steps):
        action = rng.choice(["claim", "claim", "started", "events", "events", "events", "replay", "gap", "conflict",
                             "renew", "renew", "complete", "expire", "tick", "tick", "restart"])
        name = rng.choice(list(computers))
        runner_row, bots = computers[name]
        token = runner_row["token"]
        line = f"{step:2d} {action} on {name}"
        if action == "claim":
            ready(client, runner_row, bots)
            got = claim(client, runner_row)
            if got:
                turn_ids.append(got["id"])
                owner[got["id"]] = name
                line += f" -> {got['id'][:8]} (job {got['job_id'][:8]})"
            else:
                line += " -> none"
        elif action == "tick":
            now = now + timedelta(seconds=15)
            time_machine.move_to(now.strftime("%Y-%m-%dT%H:%M:%SZ"), tick=False)
            line += f" to {now.strftime('%H:%M:%S')}"
        elif action == "restart" and restarts < RESTARTS:
            restarts += 1
            stack.close()
            stack = ExitStack()
            client = stack.enter_context(TestClient(create_app(api.app.state.store.settings)))
        elif turn_ids:
            aid = rng.choice(turn_ids)
            # Its own computer usually acts; now and then the other one tries, as a confused or stale runner would.
            if rng.random() < 0.85:
                name = owner[aid]
            else:
                name = next(other for other in computers if other != owner[aid])
            runner_row, bots = computers[name]
            token = runner_row["token"]
            line += f" {aid[:8]}" + ("" if name == owner[aid] else " (other computer)")
            acked = model["acked"].setdefault(aid, {})
            next_seq = max(acked, default=0) + 1
            if action == "started":
                r = send(client, "post", f"attempts/{aid}/started", token, {"thread_id": "walk"})
                line += f" -> {r.status_code}"
            elif action == "events":
                text = f"{aid[:4]} {next_seq}"
                batch = [{"seq": next_seq, "kind": "delta", "payload": {"text": text}}]
                r = send(client, "post", f"attempts/{aid}/events", token, {"events": batch})
                line += f" -> {r.status_code}"
                if r.status_code == 200:
                    acked[next_seq] = text
                    model["batches"][aid] = batch
            elif action == "replay":
                batch = model["batches"].get(aid)
                if batch:
                    r = send(client, "post", f"attempts/{aid}/events", token, {"events": batch})
                    line += f" -> {r.status_code}"
                else:
                    line += " (nothing to replay)"
            elif action == "gap":
                batch = [{"seq": next_seq + 1, "kind": "delta", "payload": {"text": "gap"}}]
                r = send(client, "post", f"attempts/{aid}/events", token, {"events": batch})
                line += f" -> {r.status_code}"
            elif action == "conflict":
                if acked:
                    seq = rng.choice(sorted(acked))
                    batch = [{"seq": seq, "kind": "delta", "payload": {"text": "different " + aid[:4]}}]
                    r = send(client, "post", f"attempts/{aid}/events", token, {"events": batch})
                    line += f" seq {seq} -> {r.status_code}"
                else:
                    line += " (nothing acked yet)"
            elif action == "renew":
                r = send(client, "post", f"attempts/{aid}/renew", token)
                line += f" -> {r.status_code}"
            elif action == "complete":
                outcome = rng.choice(["completed", "failed"])
                r = send(client, "post", f"attempts/{aid}/complete", token,
                         {"outcome": outcome, "last_seq": next_seq - 1, "text": "done"})
                line += f" {outcome} -> {r.status_code}"
            elif action == "expire":
                expire(api, aid)
        log.append(line)
        problems = check(api, model, log)
        if problems:
            break
    stack.close()
    return problems, log


@pytest.mark.parametrize("seed", SEEDS)
def test_a_seeded_random_walk_over_claims_events_renewals_and_restarts_keeps_every_invariant(api, time_machine, seed):
    problems, log = walk(api, time_machine, seed)
    assert not problems, f"seed {seed}: {problems}\n" + "\n".join(log)
