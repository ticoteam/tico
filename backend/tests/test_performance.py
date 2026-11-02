"""Computer performance diagnostics: on by default with an off switch for the operator or an admin; every person and
bot reads them; ten loaded minutes record an overload naming what ran; minutes roll into ten-minute points after two
days and everything goes after fourteen."""
import time

from backend import models as M
from backend import performance as P
from backend.auth import Identity
from backend.store import H
from backend.tests.test_api import api, get, headers, post, runner, setup_attempt  # noqa: F401  (the api fixture)
from backend.tests.test_bot_access import world  # noqa: F401  (a Legal bot and who may see it)


def sample(at, cpu=20.0, mem=0.5, procs=()):
    return {"at": at, "cpu": cpu, "cores": 8, "load": [1.0, 1.0, 1.0], "mem_total": 1000, "mem_used": int(1000 * mem),
            "disk": [{"volume": "state", "total_bytes": 100, "free_bytes": 50}], "active": 1,
            "procs": [{"name": n, "cpu": c, "mem": 10, "count": 1} for n, c in procs]}


def beat(api, r):
    return post(api, "runners/heartbeat", {"version": "test", "platform": "test", "readiness": {}}, token=r["token"])


def test_on_by_default_everyone_reads_it_and_only_the_operator_or_an_admin_switches_it_off(api):
    r, _, attempt = setup_attempt(api)
    rid = r["runner_id"]
    assert beat(api, r)["performance"] is True
    now = time.time()
    # A container with no memory limit: its own CPU, the host's memory.
    docker = sample(now) | {"scope": "container", "memory_scope": "host"}
    assert post(api, "runners/performance", {"samples": [sample(now - 30), docker]}, token=r["token"]) == {"kept": 2}
    api.app.state.store.settings.test_identities["ops-bot"] = Identity("bot:ops", "bot", runner_id=rid, attempt_id=attempt["id"])
    for token in ("cara-test", "ops-bot"):                      # a member and a bot both read it
        seen = get(api, f"computers/{rid}/performance?hours=1", token=token)
        assert seen["performance"] == "on" and seen["points"] and seen["points"][0][1] == 20.0
        assert seen["scope"] == "container_cpu"
        assert [x["id"] for x in seen["runs"]] == [attempt["id"]]    # the server's own record of what ran
    post(api, f"computers/{rid}/performance", {"mode": "off"}, token="cara-test", expected=403)
    post(api, f"computers/{rid}/performance", {"mode": "off"}, token="ops-bot", expected=403)
    assert post(api, f"computers/{rid}/performance", {"mode": "off"}) == {"performance": "off"}
    assert beat(api, r)["performance"] is False
    refused = post(api, "runners/performance", {"samples": [sample(now)]}, token=r["token"], expected=409)
    assert refused["error"]["code"] == "performance_off"
    assert next(m for m in get(api, "operations")["computers"] if m["id"] == rid)["performance"] == "off"
    # A process name is the executable's, never its arguments.
    post(api, f"computers/{rid}/performance", {"mode": "on"})
    bad = sample(now)
    bad["procs"] = [{"name": "node --token=abc", "cpu": 1, "mem": 1}]
    post(api, "runners/performance", {"samples": [bad]}, token=r["token"], expected=422)
    assert api.delete(f"/api/v2/computers/{rid}/performance", headers=headers("cara-test")).status_code == 403
    assert api.delete(f"/api/v2/computers/{rid}/performance", headers=headers()).json() == {"erased": True}
    assert get(api, f"computers/{rid}/performance?hours=1")["points"] == []


def test_ten_loaded_minutes_record_an_overload_that_names_what_ran_and_health_shows_it(api):
    r, _, attempt = setup_attempt(api)
    rid = r["runner_id"]
    start = (int(time.time()) // 60 - 12) * 60
    with api.app.state.store.transaction() as c:                # the turn began before the load did
        c.execute("UPDATE attempts SET created_at=? WHERE id=?", (P._iso(start - 60), attempt["id"]))
    busy = [sample(start + 60 * i, cpu=97.0, procs=[("cargo", 400.0), ("claude", 50.0)]) for i in range(10)]
    post(api, "runners/performance", {"samples": busy[:9]}, token=r["token"])
    assert get(api, f"computers/{rid}/performance?hours=1")["overloads"] == []          # nine minutes are not ten
    post(api, "runners/performance", {"samples": busy[9:]}, token=r["token"])
    seen = get(api, f"computers/{rid}/performance?hours=1")
    (o,) = seen["overloads"]
    assert o["reasons"] == ["cpu"] and o["ended_at"] is None and o["bots"][0]["bot_id"] == "ops"
    assert o["procs"][0]["name"] == "cargo"
    assert seen["summary"].startswith("Overloaded once") and "cargo" in seen["summary"]
    check = next(x for x in get(api, "health")["checks"] if x["id"] == "performance:" + rid)
    assert check["status"] == "warn" and "overloaded now" in check["summary"]
    post(api, "runners/performance", {"samples": [sample(start + 600, cpu=85.0)]}, token=r["token"])   # a calm minute ends it
    assert get(api, f"computers/{rid}/performance?hours=1")["overloads"][0]["ended"] == start + 600
    # ... until the rest of that minute arrives loaded (85% then 97%): the same overload carries on, not a second one.
    post(api, "runners/performance", {"samples": [sample(start + 630, cpu=97.0)]}, token=r["token"])
    (o,) = get(api, f"computers/{rid}/performance?hours=1")["overloads"]
    assert (o["started_at"], o["ended"], o["last"]) == (start, None, start + 600)
    post(api, "runners/performance", {"samples": [sample(start + 660)]}, token=r["token"])
    assert [x["ended"] for x in get(api, f"computers/{rid}/performance?hours=1")["overloads"]] == [start + 660]


def test_minutes_roll_into_ten_minute_points_after_two_days_and_everything_goes_after_fourteen(api):
    r, _, _ = setup_attempt(api)
    rid = r["runner_id"]
    store = api.app.state.store
    now = int(time.time()) // 600 * 600
    old = now - 3 * 86400
    with store.transaction() as c:
        P.ingest(c, rid, [P_sample(old + 30 * i, cpu=10.0 * (i + 1)) for i in range(4)], now=old)
        P.ingest(c, rid, [P_sample(now - 15 * 86400)], now=now - 15 * 86400)
    P.sweep(store, now)
    with store.read() as c:
        rows = [tuple(x) for x in c.execute("SELECT span,created_at,n,cpu,cpu_max FROM perf_points WHERE runner_id=?", (rid,))]
        procs = [tuple(x) for x in c.execute("SELECT span,key,n FROM perf_usage WHERE runner_id=?", (rid,))]
    assert rows == [(600, old // 600 * 600, 4, 25.0, 40.0)]
    assert procs == [(600, "git", 4)]
    week = get(api, f"computers/{rid}/performance?hours=168")
    assert week["span"] == 600 and week["points"][0][1] == 25.0


def P_sample(at, cpu=20.0):
    from backend.models import PerformanceSample
    return PerformanceSample(**sample(at, cpu=cpu, procs=[("git", 5.0)]))


def test_a_support_attachment_carries_the_window_with_computers_and_bots_relabelled(api):
    from backend import diagnostics as D
    r, _, attempt = setup_attempt(api)
    now = int(time.time())
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE attempts SET created_at=? WHERE id=?", (P._iso(now - 3600), attempt["id"]))
        P.ingest(c, r["runner_id"], [P_sample(now - 60), P_sample(now)], now=now)
    with api.app.state.store.read() as c:
        section = D._performance(c, 24)
    redactor = D.Redactor(bots=[("ops", ["Ops"])])
    bundle = redactor.clean(D.allowed({"format": D.FORMAT, "performance": section}))
    (computer,) = bundle["performance"]["computers"]
    assert computer["label"] == "runner-1" and computer["points"] and computer["bots"] == [
        {"bot_id": "bot-1", "model": "", "runs": 1}]
    assert r["runner_id"] not in D.canonical(bundle) and "ops" not in D.canonical(bundle)


def test_a_bot_the_reader_may_not_see_is_unnamed_in_turns_overloads_and_the_summary(world):
    from backend.tests.test_bot_access import set_access
    api, tokens, _ = world
    r, _, attempt = setup_attempt(api)
    rid = r["runner_id"]
    set_access(api, see={"teams": ["legal", "sales"]}, read={"teams": ["legal"]}, write={"teams": ["legal"]})
    start = (int(time.time()) // 60 - 12) * 60
    with api.app.state.store.transaction() as c:               # the Legal bot's turn ran on this computer
        counsel = H.resolve_bot(c, "counsel")["id"]
        c.execute("UPDATE attempts SET bot_id=?,created_at=? WHERE id=?", (counsel, P._iso(start - 60), attempt["id"]))
        c.execute("INSERT OR REPLACE INTO turns(id,bot,model) VALUES(?,?,'legal-model-x')",
                  (attempt["id"], "bot:" + counsel))
    post(api, "runners/performance", {"samples": [sample(start + 60 * i, cpu=97.0) for i in range(10)]}, token=r["token"])
    cara = get(api, f"computers/{rid}/performance?hours=1", token="cara-test")
    assert cara["runs"][0]["bot"] == "counsel" and cara["runs"][0]["id"] == attempt["id"]
    assert cara["overloads"][0]["bots"][0]["bot"] == "counsel" and "counsel" in cara["summary"]
    dee = get(api, f"computers/{rid}/performance?hours=1", token=tokens["dee"])     # Sales sees the bot, cannot read it
    assert dee["runs"][0] == dee["runs"][0] | {"bot": "counsel", "model": "legal-model-x", "id": None}
    seen = get(api, f"computers/{rid}/performance?hours=1", token=tokens["stranger"])
    assert seen["runs"][0] == seen["runs"][0] | {"bot": "", "model": "", "id": None}
    assert seen["busiest"]["bots"] == [{"bot": "", "model": "", "runs": 1}]
    assert seen["overloads"][0]["bots"] == [{"bot": "", "model": "", "runs": 1}]
    assert "counsel" not in str(seen) and "legal-model-x" not in str(seen) and "another bot" in seen["summary"]
    # A long list of bots the reader sees is cut, never the row for those it does not.
    many = [{"bot": f"b{i:02}", "model": "", "runs": 2} for i in range(25)] + [{"bot": "counsel", "model": "", "runs": 1}]
    shown = P._shown(many, {f"b{i:02}": {"see": True} for i in range(25)}, 20)
    assert len(shown) == 21 and shown[-1] == {"bot": "", "model": "", "runs": 1}


def test_a_fast_clock_or_a_backlog_still_judges_every_minute(api):
    r, _, _ = setup_attempt(api)
    rid = r["runner_id"]
    now = int(time.time()) // 60 * 60
    # A sample three hours ahead counts as now, and does not hold back the loaded minutes that follow.
    post(api, "runners/performance", {"samples": [sample(now + 3 * 3600)]}, token=r["token"])
    start = now + 60
    with api.app.state.store.transaction() as c:
        assert c.execute("SELECT max(created_at) FROM perf_points WHERE runner_id=?", (rid,)).fetchone()[0] <= time.time()
        P.ingest(c, rid, [P_sample(start + 60 * i, cpu=97.0) for i in range(10)], now=start + 600)
        # A late minute from inside the overload changes nothing, open or ended.
        P.ingest(c, rid, [P_sample(start + 300, cpu=97.0)], now=start + 600)
        assert c.execute("SELECT last_seen_at FROM perf_overloads WHERE runner_id=?", (rid,)).fetchone()[0] == start + 540
        # A backlog: two calm minutes then a loaded one, in one batch. The calm minute ends the overload.
        P.ingest(c, rid, [P_sample(start + 600, cpu=10.0), P_sample(start + 660, cpu=10.0), P_sample(start + 720, cpu=97.0)],
                 now=start + 720)
        P.ingest(c, rid, [P_sample(start + 540, cpu=97.0)], now=start + 720)
        rows = [dict(x) for x in c.execute("SELECT started_at,ended_at FROM perf_overloads WHERE runner_id=?", (rid,))]
    assert rows == [{"started_at": start, "ended_at": start + 600}]


def test_turning_it_off_ends_an_open_overload(api):
    r, _, _ = setup_attempt(api)
    rid = r["runner_id"]
    start = (int(time.time()) // 60 - 11) * 60
    post(api, "runners/performance", {"samples": [sample(start + 60 * i, cpu=97.0) for i in range(10)]}, token=r["token"])
    assert get(api, f"computers/{rid}/performance?hours=1")["overloads"][0]["ended_at"] is None
    post(api, f"computers/{rid}/performance", {"mode": "off"})
    assert get(api, f"computers/{rid}/performance?hours=1")["overloads"][0]["ended"] == start + 600
    assert not any(x["id"] == "performance:" + rid and "overloaded now" in x["summary"] for x in get(api, "health")["checks"])


def test_a_bot_discovers_computers_and_reads_performance_by_label_or_id(api):
    """The computer list lets bots discover computers; performance is every bot's to read, by label or id. An unknown
    name and a removed computer's get the same answer, and a caller who may not read it learns nothing either way."""
    from backend.tests.test_mcp import call
    r, _, attempt = setup_attempt(api)
    rid = r["runner_id"]
    post(api, "runners/performance", {"samples": [sample(time.time() - 30)]}, token=r["token"])
    gone = runner(api, label="Old Mac")["runner_id"]
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE runners SET revoked_at=? WHERE id=?", (H.now(), gone))
        c.execute("UPDATE runners SET accepts_member_bots=0 WHERE id=?", (rid,))    # not opened to members' bots
    bot = attempt["token"]
    assert rid in {x["id"] for x in get(api, "computers", token=bot)["computers"]}
    for wanted in ("Test Mac", "test mac ", rid):
        failed, seen = call(api, "hub_computer_performance", {"computer": wanted, "hours": 1}, token=bot)
        assert not failed, seen
        assert seen["computer"] == {"id": rid, "label": "Test Mac"} and seen["points"][0][1] == 20.0
    for wanted in ("No such box", "Old Mac", gone):
        failed, seen = call(api, "hub_computer_performance", {"computer": wanted}, token=bot)
        assert failed and f"No computer named {wanted!r}; hub computer list names them" in str(seen), seen
    for wanted in ("Test Mac", "No such box"):     # a runner credential is neither a person nor a bot
        assert api.get(f"/api/v2/computers/{wanted}/performance", headers=headers(r["token"])).status_code == 403
    # Two computers whose labels match once case and spaces are ignored: the label is refused, each id still reads its own.
    twins = [runner(api, label=label) for label in ("Shared Mac", "Shared Mac 2")]
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE runners SET label=' shared MAC ' WHERE id=?", (twins[1]["runner_id"],))
    for twin, cpu in zip(twins, (31.0, 62.0)):
        post(api, "runners/performance", {"samples": [sample(time.time() - 30, cpu=cpu)]}, token=twin["token"])
    failed, seen = call(api, "hub_computer_performance", {"computer": "Shared Mac"}, token=bot)
    assert failed and "No computer named 'Shared Mac'; more than one has that label, use its id" in str(seen), seen
    for twin, cpu in zip(twins, (31.0, 62.0)):
        failed, seen = call(api, "hub_computer_performance", {"computer": twin["runner_id"], "hours": 1}, token=bot)
        assert not failed and seen["computer"]["id"] == twin["runner_id"] and seen["points"][0][1] == cpu, seen


def test_an_open_overload_reads_only_the_turns_of_its_window_however_long_the_computers_history(api):
    """Dev 249: every post during an open overload names what ran (`_runs`) under the write lock. It visited every turn
    the computer had ever run, one table row each, and on a slow disk that held the lock ~100 s. The work now depends
    on the window's turns, not on the history, and the turns it names are the same."""
    r, _, attempt = setup_attempt(api)
    rid, store = r["runner_id"], api.app.state.store
    start = (int(time.time()) // 60 - 12) * 60
    columns = "id,job_id,bot_id,runner_id,generation,token_hash,state,lease_until,created_at,started_at,finished_at"

    def turn(c, n, began, ended, state="completed"):
        c.execute(f"INSERT INTO attempts({columns},lane) SELECT ?,job_id,bot_id,runner_id,generation,?,?,lease_until,?,?,?,? "
                  "FROM attempts WHERE id=?", (f"old-{n}", f"tok-{n}", state, P._iso(began), P._iso(began),
                                                ended and P._iso(ended), f"lane-{n}", attempt["id"]))

    with store.transaction() as c:
        c.execute("UPDATE attempts SET created_at=?,started_at=? WHERE id=?", (P._iso(start - 60), P._iso(start - 60), attempt["id"]))
        turn(c, "before", start - 7200, start - 3600)            # finished before the window: not named
        turn(c, "during", start - 600, start + 120)              # overlaps it
        turn(c, "after", start + 3600, start + 4000)             # began after it
        turn(c, "open", start - 86400, None, "running")          # still running since yesterday
    busy = [sample(start + 60 * i, cpu=97.0) for i in range(10)]
    post(api, "runners/performance", {"samples": busy}, token=r["token"])          # the overload opens
    with store.read() as c:
        assert {x["id"] for x in P._runs(c, rid, start, start + 660)} == {attempt["id"], "old-during", "old-open"}

    def work(history):
        """VM steps of one loaded post while the overload is open, with `history` older turns on the computer."""
        with store.transaction() as c:
            c.execute("DELETE FROM attempts WHERE id LIKE 'hist-%'")
            for n in range(history):
                began = start - 30 * 86400 + n * 60
                c.execute(f"INSERT INTO attempts({columns}) SELECT ?,job_id,bot_id,runner_id,generation,?,'completed',"
                          "lease_until,?,?,? FROM attempts WHERE id=?",
                          (f"hist-{n}", f"htok-{n}", P._iso(began), P._iso(began), P._iso(began + 30), attempt["id"]))
        steps = [0]

        def count():
            steps[0] += 1
        with store.transaction() as c:
            c.set_progress_handler(count, 100)
            try:
                P.ingest(c, rid, M.PerformanceBatch(samples=[sample(start + 600, cpu=97.0)]).samples, now=start + 630)
            finally:
                c.set_progress_handler(None, 0)
            c.rollback()
        return steps[0]

    few, many = work(20), work(4000)
    assert many < few * 2 + 50, (few, many)        # before the fix: 50 times as many (8 and 404)
    with store.read() as c:
        plan = " ".join(row[3] for row in c.execute(
            "EXPLAIN QUERY PLAN SELECT rowid FROM attempts WHERE runner_id=? AND finished_at>=?", (rid, P._iso(start))))
    assert "attempts_runner_finished" in plan


def test_steering_polls_and_output_batches_store_no_retry_receipts_and_replay_the_same(api):
    r, _, attempt = setup_attempt(api)
    aid = attempt["id"]
    post(api, f"attempts/{aid}/started", {"thread_id": "t"}, token=r["token"])

    def receipts():
        with api.app.state.store.read() as c:
            return c.execute("SELECT count(*) FROM idempotency WHERE operation LIKE ?", (f"/api/v2/attempts/{aid}/%",)
                             ).fetchone()[0]
    before = receipts()
    for _ in range(3):
        assert post(api, f"attempts/{aid}/inputs", {}, token=r["token"])["messages"] == []
    batch = {"events": [{"seq": 1, "kind": "delta", "payload": {"text": "Hel"}},
                        {"seq": 2, "kind": "delta", "payload": {"text": "lo"}}]}
    first = post(api, f"attempts/{aid}/events", batch, token=r["token"], key="batch-1")
    # A lost reply retried with the same key answers the same, from the sequence numbers already stored.
    assert post(api, f"attempts/{aid}/events", batch, token=r["token"], key="batch-1") == first
    assert first["ack_seq"] == 2
    assert receipts() == before, "an empty poll or an output batch needs no stored receipt"
    with api.app.state.store.read() as c:
        assert c.execute("SELECT count(*) FROM attempt_events WHERE attempt_id=?", (aid,)).fetchone()[0] == 2


def test_one_tasks_history_of_one_field_reads_by_task(api):
    with api.app.state.store.read() as c:
        for sql in ("SELECT coalesce(max(rowid),0) FROM task_events WHERE task_id=? AND field='status' "
                    "AND old IN ('done','closed','declined') AND new NOT IN ('done','closed','declined')",
                    "SELECT * FROM task_events WHERE task_id=? AND field='owner' ORDER BY created_at DESC, rowid DESC LIMIT 1"):
            plan = " ".join(row[3] for row in c.execute("EXPLAIN QUERY PLAN " + sql, ("t",)))
            assert "task_events_task_field" in plan, plan


def test_a_steering_poll_with_nothing_queued_answers_without_the_writer(api, monkeypatch):
    r, origin, attempt = setup_attempt(api)
    aid = attempt["id"]
    post(api, f"attempts/{aid}/started", {"thread_id": "t"}, token=r["token"])
    store = api.app.state.store
    write = store.transaction

    def no_writer(*a, **k):
        raise AssertionError("an idle steering poll took the writer gate")
    monkeypatch.setattr(store, "transaction", no_writer)
    for _ in range(3):
        assert post(api, f"attempts/{aid}/inputs", {}, token=r["token"]) == {"attempt_id": aid, "messages": []}
    monkeypatch.setattr(store, "transaction", write)
    # A follow-up in the turn's chat is queued work: the poll takes it as the write, and redelivers it from a
    # read until the runtime acknowledges it.
    follow = post(api, "chat/ops", {"text": "Include the archive"})
    assert [m["id"] for m in post(api, f"attempts/{aid}/inputs", {}, token=r["token"])["messages"]] == [follow["id"]]
    monkeypatch.setattr(store, "transaction", no_writer)
    assert [m["id"] for m in post(api, f"attempts/{aid}/inputs", {}, token=r["token"])["messages"]] == [follow["id"]]
    monkeypatch.setattr(store, "transaction", write)
    # A stale lease is refused from the read as it was from the write.
    with store.transaction() as c:
        c.execute("UPDATE attempts SET lease_until='2000-01-01T00:00:00Z' WHERE id=?", (aid,))
    assert api.post(f"/api/v2/attempts/{aid}/inputs", json={}, headers=headers(r["token"])).status_code == 409
