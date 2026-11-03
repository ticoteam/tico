"""Temporary probe (not for merge): which call sites of /api/v2/health and /api/status issue statements that grow,
and which statements scan whole tables."""
import collections
import contextlib
import json
import os
import sys

from backend.store import H
from backend.tests.test_api import api  # noqa: F401  (the fixture)
from backend.tests.test_api import assign, headers, post, runner
from backend.tests.test_claim_statements import claim, handed_run, seed
from backend.tests.test_getting_started import heartbeat

RUNTIMES = {"codex": {"installed": True, "authenticated": "ready"},
            "claude": {"installed": True, "authenticated": "ready"}}
OLD = H.shift(H.now(), hours=-5)
PR_OLD = H.shift(H.now(), hours=-13)
HOUR_AGO = H.shift(H.now(), minutes=-10)


def site():
    frames, f = [], sys._getframe(2)
    while f is not None and len(frames) < 5:
        name = f.f_code.co_filename
        if "/backend/" in name and "/backend/tests/" not in name:
            frames.append(f"{os.path.basename(name)}:{f.f_code.co_name}:{f.f_lineno}")
        f = f.f_back
    return " < ".join(frames) or "?"


@contextlib.contextmanager
def profiled(api, sites, texts):
    store = api.app.state.store
    original = store.read

    @contextlib.contextmanager
    def borrowed():
        with original() as c:
            def trace(sql):
                key = site()
                sites[key] += 1
                texts.setdefault(key, sql)
            c.set_trace_callback(trace)
            try:
                yield c
            finally:
                c.set_trace_callback(None)
    store.read = borrowed
    try:
        yield
    finally:
        del store.read


def measure(api, route):
    sites, texts = collections.Counter(), {}
    path = "/api/v2/health" if route == "health" else "/api/status"
    with profiled(api, sites, texts):
        response = api.get(path, headers=headers())
    assert response.status_code == 200, response.text
    return sum(sites.values()), sites, texts


def work(api, machine, rounds):
    """Real claims and completions: attempts for the machine's bots."""
    for _ in range(rounds):
        answer, _ = claim(api, machine)
        if not answer.get("attempt"):
            break
        handed_run(api, machine, answer)


def offline(api, prefix, count):
    """`count` bots on a computer that never reported in, each with a queued chat."""
    machine = runner(api, label=f"Offline {prefix}")
    slugs = [f"{prefix}-{n:02d}" for n in range(count)]
    store = api.app.state.store
    with store.transaction() as c:
        H.sync_registry(c, {s: {"name": s, "runtime": "fake", "status": "active"} for s in slugs},
                        {"people": [{"id": p, "email": p + "@acme.example"} for p in ("ana", "ben")]})
        for s in slugs:
            c.execute("INSERT INTO bot_config(bot,config,owner_id) VALUES(?,?,'ana')",
                      (s, '{"name": "%s", "runtime": "fake"}' % s))
    for s in slugs:
        assign(api, machine, s)
    for s in slugs:
        post(api, "chat/" + s, {"text": "Please summarize the current work."})


def scenario(name, fn):
    try:
        fn()
    except Exception as error:      # noqa: BLE001 - a probe reports what it cannot build and goes on
        print(f"SCENARIO {name} FAILED: {type(error).__name__}: {str(error)[:300]}", flush=True)


def populate(api, prefix, k):
    store = api.app.state.store
    machine = seed(api, prefix, k)                                  # online computer, k bots, a queued chat each
    bots = [f"{prefix}-{n:02d}" for n in range(k)]
    scenario("claims", lambda: work(api, machine, k))              # attempts: completed, one more queued chat each
    scenario("offline", lambda: offline(api, prefix + "off", k))    # bots waiting on a computer that never reported
    scenario("fleet", lambda: [heartbeat(api, runner(api, label=f"{prefix} Mac {n:02d}")["runner_id"], seconds_ago=5,
                                         runtimes=RUNTIMES) for n in range(k)])

    def ages_and_states():
        with store.transaction() as c:
            ids = {slug: c.execute("SELECT id FROM bots WHERE slug=?", (slug,)).fetchone()[0] for slug in bots}
            marks = lambda part: ",".join("?" * len(part))  # noqa: E731
            aged, uncertain = [ids[s] for s in bots[1::3]], [ids[s] for s in bots[0::3]]
            if aged:
                c.execute(f"UPDATE jobs SET created_at=? WHERE bot_id IN ({marks(aged)}) AND state='queued'",
                          (OLD, *aged))
            if uncertain:
                c.execute(f"UPDATE jobs SET state='uncertain', created_at=? WHERE bot_id IN ({marks(uncertain)}) "
                          "AND state='queued'", (OLD, *uncertain))
            for slug in bots[2::3]:
                c.execute("INSERT OR REPLACE INTO bot_status(bot,state,changed_at,updated_at) VALUES(?,?,?,?)",
                          (ids[slug], "limited", HOUR_AGO, HOUR_AGO))
            fallback = [ids[s] for s in bots[: max(1, k // 2)]]
            c.execute(f"UPDATE attempts SET result=? WHERE bot_id IN ({marks(fallback)}) AND state='completed'",
                      ('{"fallback": "codex"}', *fallback))
            for slug in bots[-max(1, k // 5):]:
                c.execute("UPDATE bots SET state='quarantined' WHERE id=?", (ids[slug],))
            doc = {"schema_version": 1, "runtimes": RUNTIMES, "bots": {}}
            for n, slug in enumerate(bots):
                doc["bots"][ids[slug]] = {
                    "ready": n % 2 == 0, "runtime": "codex",
                    "problems": [] if n % 2 == 0 else ["Codex is not signed in"],
                    "tools": [{"env": "STRIPE_KEY", "credential": "missing", "service": "stripe"}],
                    "warnings": ["Claude Code settings deny: gh pr create"] if n % 5 == 0 else []}
            c.execute("UPDATE runners SET readiness=?, last_seen_at=? WHERE id=?",
                      (json.dumps(doc), H.now(), machine["runner_id"]))
    scenario("ages_and_states", ages_and_states)

    def stalls():
        from backend.tests.test_health_statements import stall
        stall(api, k, prefix + "s", blocked=max(1, k // 3))
    scenario("stalls", stalls)

    def loops():
        with store.transaction() as c:
            for n in range(max(1, k // 2)):
                tid = f"{prefix}-loop{n}"
                c.execute("INSERT INTO tasks(id,title,requester_id,owner_id,status,created_at,updated_at,private) "
                          "VALUES(?,?,?,?,?,?,?,0)", (tid, "Looping " + tid, "human:ana", bots[0], "open", OLD, OLD))
                for j in range(22):
                    c.execute("INSERT INTO task_events(id,task_id,created_at,actor,field,old,new) "
                              "VALUES(?,?,?,?,?,?,?)", (f"{tid}-e{j}", tid, H.shift(H.now(), minutes=-j),
                                                       "human:ana", "status", "a", "b"))
    scenario("loops", loops)

    def waiting_on_people():
        with store.transaction() as c:
            for n in range(k):
                tid = f"{prefix}-wait{n}"
                c.execute("INSERT INTO tasks(id,title,requester_id,owner_id,status,created_at,updated_at,private,"
                          "waiting_on) VALUES(?,?,?,?,?,?,?,0,?)",
                          (tid, "Waiting " + tid, "human:ana", "bot:" + bots[0], "waiting", OLD, OLD, "human:ben"))
    scenario("waiting_on_people", waiting_on_people)

    def aged_prs():
        with store.transaction() as c:
            for n in range(k):
                tid = f"{prefix}-pr{n}"
                c.execute("INSERT INTO tasks(id,title,requester_id,owner_id,status,created_at,updated_at,private) "
                          "VALUES(?,?,?,?,?,?,?,0)", (tid, "PR " + tid, "human:ana", "bot:" + bots[n], "open", OLD, OLD))
                detail = {"head_sha": "h" + str(n), "health_wait": {"reason": "review", "since": PR_OLD}}
                c.execute("INSERT INTO task_links(id,task_id,kind,url,title,state,added_by,created_at,mergeable,"
                          "checks,review_state,detail,number,branch,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                          (f"{tid}-link", tid, "pr", f"https://github.com/acme/app/pull/{n}", "PR", "open",
                           "bot:" + bots[n], OLD, "clean", "passing", "", json.dumps(detail), n + 1, f"b{n}", OLD))
    scenario("aged_prs", aged_prs)

    def worktree_events():
        with store.transaction() as c:
            for n in range(k):
                c.execute("INSERT INTO events(id,created_at,actor,action,target,detail) VALUES(?,?,?,?,?,?)",
                          (f"{prefix}-rel{n}", HOUR_AGO, "x", "worktree.released", f"{prefix}-t{n}",
                           '{"reason": "done"}'))
    scenario("worktree_events", worktree_events)
    return machine


def plans(store, sql):
    with store.read() as c:
        return [(r[-1]) for r in c.execute("EXPLAIN QUERY PLAN " + sql).fetchall()]


def report(api, route, small, large):
    small_total, small_sites, _ = small
    large_total, large_sites, large_texts = large
    store = api.app.state.store
    print(f"\n===== ROUTE {route}: total small={small_total} large={large_total} delta={large_total - small_total}",
          flush=True)
    print("-- top sites (large, small in brackets)", flush=True)
    for key, n in large_sites.most_common(40):
        print(f"  {n:4d} [{small_sites[key]:3d}] {key}", flush=True)
    print("-- growth (large - small)", flush=True)
    deltas = sorted(((large_sites[k] - small_sites[k], k) for k in set(small_sites) | set(large_sites)), reverse=True)
    for delta, key in deltas[:40]:
        if delta > 0:
            print(f"  +{delta:4d}  {key}", flush=True)
            sql = large_texts.get(key, "")
            if sql.lstrip().upper().startswith(("SELECT", "WITH")):
                try:
                    print("        PLAN " + " | ".join(plans(store, sql))[:300], flush=True)
                except Exception as error:      # noqa: BLE001
                    print("        PLAN failed " + str(error)[:120], flush=True)
            print("        SQL  " + sql[:200].replace("\n", " "), flush=True)
    print("-- full scans (every large site)", flush=True)
    for key in large_sites:
        sql = large_texts.get(key, "")
        if not sql.lstrip().upper().startswith(("SELECT", "WITH")):
            continue
        try:
            lines = plans(store, sql)
        except Exception as error:      # noqa: BLE001
            print(f"  explain failed {key}: {str(error)[:100]}", flush=True)
            continue
        scans = [line for line in lines if line.startswith("SCAN ") and "json_each" not in line
                 and "CONSTANT" not in line and "SUBQUERY" not in line]
        if scans:
            print(f"  {large_sites[key]:4d} {key}\n        {' | '.join(scans)[:260]}", flush=True)


def test_probe(api):
    populate(api, "p1", 3)
    for route in ("health", "status"):
        measure(api, route)
    small = {route: measure(api, route) for route in ("health", "status")}
    populate(api, "p2", 30)
    for route in ("health", "status"):
        measure(api, route)
    large = {route: measure(api, route) for route in ("health", "status")}
    for route in ("health", "status"):
        report(api, route, small[route], large[route])
