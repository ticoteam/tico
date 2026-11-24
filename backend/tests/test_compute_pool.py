from concurrent.futures import ThreadPoolExecutor

import pytest

from backend import (
    bot_settings as BS,
    bot_stops,
    cloud_machines,
    compute_pool,
    execution,
    models,
    models_preset,
    plan_exits,
    providers,
    start_places,
)
from backend.scheduler import Scheduler
from backend.store import H, digest, encode
from backend.tests.helpers.api import assign, claim, expire, get, post, put, runner, settled
from backend.tests.helpers.cloud_machines import SIZES, FakeHQ
from backend.tests.helpers.login_holds import events
from backend.tests.helpers.model_subscriptions import add, on, pick

SWITCH = "settings/burst-rehome"
AUTOSCALE = "settings/burst-autoscale"
PREP = "runners/assignments?burst=true"


def heartbeat(
    api,
    runner,
    entry=None,
    *,
    runtime="codex",
    signed_in=True,
    capable=True,
    worktrees=False,
    bots=None,
    lease=False,
    revision=None,
):
    config = entry["config"] if entry else {}
    runtime = config.get("runtime", runtime)
    harness = config.get("harness", runtime)
    installed = {"installed": True, "authenticated": "ready"}
    manifest = {"claude": "claude-code"}.get(harness, harness)
    runtimes = {"pi": installed, runtime: {**installed, "authenticated": "ready" if signed_in else "missing"}}
    bot = {
        "ready": signed_in,
        "runtime": runtime,
        "harness": harness,
        "published": True,
        "repository_present": True,
        "configuration_valid": True,
    }
    if entry:
        bot["burst_preparation"] = entry["burst_preparation"]
    if revision:
        bot["repository_revision"] = revision
    body = {
        "version": "test",
        "platform": "test",
        "capabilities": ["attempt_credentials_v1", execution.FRESH_SESSION]
        + (["burst_preparation_v1"] if capable else [])
        + ([compute_pool.LEASE] if lease else []),
        "readiness": {
            "schema_version": 1,
            "isolated": True,
            "runtimes": runtimes,
            "harnesses": {"pi": installed, manifest: installed},
            "bots": bots if bots is not None else {entry["bot"] if entry else "ops": bot},
            "worktrees": worktrees,
        },
    }
    post(api, "runners/heartbeat", body, token=runner["token"])
    return body


def machine(api, key):
    store = api.app.state.store
    with store.transaction() as c:
        c.execute(
            "INSERT INTO cloud_machines(bot,machine_id,state,placement,heartbeat_at,ttl_s) "
            "VALUES(?,?,'running','own',?,300)",
            (key, key, cloud_machines.now()),
        )
        code = cloud_machines.mint(c, key, "ana")
    result = post(api, "runners/enroll", {"code": code, "label": key, "platform": "test"})
    heartbeat(api, result)
    return result


@pytest.fixture
def fleet(api):
    store = api.app.state.store
    base, burst = machine(api, "_shared"), machine(api, "_burst:1")
    on(api, "ops", "codex", providers.recommended("openai", "codex"))
    assign(api, base, "ops")
    with store.transaction() as c:
        c.execute("UPDATE bot_config SET repo='acme/ops' WHERE bot='ops'")
    return store, base, burst


def queue(api, text="Take the next turn"):
    return post(api, "chat/ops", {"text": text})


def prepare(api, burst):
    (entry,) = get(api, PREP, token=burst["token"])
    heartbeat(api, burst, entry)
    return entry


def queue_prepared(api, burst):
    put(api, SWITCH, {"enabled": True})
    queue(api)
    return prepare(api, burst)


def placement(store):
    with store.read() as c:
        return tuple(c.execute("SELECT runner_id,generation FROM assignments WHERE bot='ops'").fetchone())


def test_setting_defaults_off_and_is_admin_only(api, fleet):
    store, base, burst = fleet
    assert get(api, SWITCH) == {"enabled": False}
    get(api, SWITCH, token="cara-test", expected=403)
    put(api, SWITCH, {"enabled": True}, token="cara-test", expected=403)
    body = heartbeat(api, burst)
    before = models.Heartbeat.model_validate(body).model_dump()
    before["readiness"]["bots"]["ops"].pop("burst_preparation", None)
    path = "/api/v2/runners/heartbeat"
    with store.transaction() as c:
        c.execute(
            "INSERT INTO idempotency(actor,operation,key,request_hash,response,created_at) VALUES(?,?,?,?,?,?)",
            ("runner:" + burst["runner_id"], path, "legacy-beat", digest(encode(before)), '{"legacy":true}', H.now()),
        )
    assert post(api, "runners/heartbeat", body, burst["token"], key="legacy-beat")["legacy"]
    queue(api)
    assert get(api, PREP, token=burst["token"]) == []
    assert claim(api, burst, "ops") is None
    assert placement(store) == (base["runner_id"], 1)
    assert claim(api, base, "ops")["generation"] == 1


def test_assignment_replays_a_key_from_before_pinning(api, fleet):
    store, base, _ = fleet
    body = {"runner_id": base["runner_id"], "expected_generation": 0}
    path = "bots/ops/assignment"
    with store.transaction() as c:
        c.execute(
            "INSERT INTO idempotency(actor,operation,key,request_hash,response,created_at) VALUES(?,?,?,?,?,?)",
            (
                "human:ana",
                "/api/v2/" + path,
                "old-assignment",
                digest(encode({**body, "on_behalf_of": None})),
                '{"generation":1}',
                H.now(),
            ),
        )
    assert post(api, path, body, key="old-assignment") == {"generation": 1}
    assert placement(store) == (base["runner_id"], 1)


def test_two_burst_claims_commit_only_one_assignment_and_reservation(api, fleet):
    store, _, burst = fleet
    other = machine(api, "_burst:2")
    queue_prepared(api, burst)
    assert get(api, PREP, token=other["token"]) == []
    with store.transaction() as c:
        c.execute("UPDATE cloud_machines SET state='starting' WHERE bot='_burst:1'")
    prepare(api, other)
    with store.transaction() as c:
        c.execute("UPDATE cloud_machines SET state='running' WHERE bot='_burst:1'")
    with ThreadPoolExecutor(max_workers=2) as pool:
        claims = list(pool.map(lambda r: claim(api, r, "ops"), (burst, other)))
    (winner,) = [attempt for attempt in claims if attempt]
    rid = (burst, other)[claims.index(winner)]["runner_id"]
    assert winner["start_reserved"] and winner["generation"] == 2
    assert placement(store) == (rid, 2)
    with store.read() as c:
        assert [
            tuple(r)
            for r in c.execute(
                "SELECT a.runner_id,a.generation,a.id,p.start_until IS NOT NULL "
                "FROM attempts a JOIN start_places p ON p.attempt_id=a.id WHERE a.bot_id='ops'"
            )
        ] == [(rid, 2, winner["id"], 1)]
        assert c.execute("SELECT placement FROM cloud_machines WHERE bot='ops'").fetchone()[0] == "shared"


@pytest.mark.parametrize("invalid", ["configuration_valid", "repository_present"])
def test_preparation_rotates_past_failed_checkouts(api, fleet, invalid):
    store, base, burst = fleet
    for bot in ("cpo", "finance"):
        on(api, bot, "codex", providers.recommended("openai", "codex"))
        assign(api, base, bot)
        with store.transaction() as c:
            c.execute("UPDATE bot_config SET repo=? WHERE bot=?", (f"acme/{bot}", bot))
    base_check = heartbeat(api, base)["readiness"]["bots"]["ops"]
    heartbeat(api, base, bots={bot: base_check for bot in ("ops", "cpo", "finance")})
    # An idle worker has no assignments or preparation reports yet.
    heartbeat(api, burst, bots={})
    put(api, SWITCH, {"enabled": True})
    for bot in ("ops", "cpo", "finance"):
        post(api, f"chat/{bot}", {"text": "Take the next turn"})

    for bot in ("ops", "cpo"):
        (entry,) = get(api, PREP, token=burst["token"])
        assert entry["bot"] == bot
        # Older runners can stamp a ticket on an invalid checkout. An origin mismatch has no ticket.
        report = (
            {**base_check, "burst_preparation": entry["burst_preparation"], invalid: False}
            if bot == "ops"
            else base_check
        )
        heartbeat(api, burst, bots={bot: report})
        assert claim(api, burst, bot) is None

    assert prepare(api, burst)["bot"] == "finance"
    assert claim(api, burst, "finance")["burst_repository"] == "acme/finance"


def test_refused_reservation_rolls_back_the_entire_claim(api, fleet, monkeypatch):
    store, base, burst = fleet
    queue_prepared(api, burst)
    monkeypatch.setattr(start_places, "admit", lambda *args: {"reserved": False})
    assert claim(api, burst, "ops") is None
    assert placement(store) == (base["runner_id"], 1)
    with store.read() as c:
        assert c.execute("SELECT count(*) FROM attempts WHERE bot_id='ops'").fetchone()[0] == 0
        assert c.execute("SELECT state FROM jobs WHERE bot_id='ops'").fetchone()[0] == "queued"


@pytest.mark.parametrize("reason", ["pin", "voice", "live", "unresolved", "worktree", "task_turn", "own_machine"])
def test_excluded_work_stays_on_base(api, fleet, reason):
    store, base, burst = fleet
    queue_prepared(api, burst)
    if reason == "pin":
        post(api, "bots/ops/assignment", {"runner_id": base["runner_id"], "expected_generation": 1, "pinned": True})
        assert get(api, "bots/ops")["assignment"]["pinned"] is True
    if reason in ("live", "unresolved"):
        active = claim(api, base, "ops")
        queue(api)
        if reason == "unresolved":
            with store.transaction() as c:
                c.execute("UPDATE jobs SET state='uncertain' WHERE id=?", (active["job_id"],))
                c.execute("UPDATE attempts SET state='expired' WHERE id=?", (active["id"],))
    with store.transaction() as c:
        if reason == "voice":
            c.execute(
                "UPDATE messages SET refs=json_set(refs,'$.live_meeting',json(?)) "
                "WHERE id IN (SELECT message_id FROM jobs WHERE bot_id='ops')",
                ('{"voice_call":"test"}',),
            )
        if reason in ("worktree", "task_turn"):
            task = H.task_create(c, "human:ana", "Work", "Keep a checkout", "bot:ops")
        if reason == "task_turn":
            c.execute(
                "UPDATE messages SET refs=json_set(refs,'$.task',?) WHERE id IN (SELECT message_id FROM jobs)",
                (task["id"],),
            )
        if reason == "worktree":
            c.execute(
                "INSERT INTO task_links(id,task_id,kind,url,created_at,state,added_by,computer_id) "
                "VALUES(?,?,'worktree','',?,'present','bot:ops',?)",
                (H.new_id(), task["id"], H.now(), base["runner_id"]),
            )
        if reason == "own_machine":
            c.execute("UPDATE cloud_machines SET placement='own' WHERE bot='ops'")
    assert get(api, PREP, token=burst["token"]) == []
    assert claim(api, burst, "ops") is None
    assert placement(store)[0] == base["runner_id"]


@pytest.mark.parametrize("outcome", ["completed", "interrupted", "expired", "late", "revoke", "gone", "no_base"])
def test_guest_returns_when_its_turn_settles(api, fleet, outcome, monkeypatch):
    store, base, burst = fleet
    queue_prepared(api, burst)
    attempt = claim(api, burst, "ops")
    assert attempt
    manager, hq = api.app.state.cloud_machines, FakeHQ()
    manager.step(hq, "ops", cloud_machines.now())
    manager.step(hq, "_burst:1", cloud_machines.now())
    assert hq.calls == [] and placement(store) == (burst["runner_id"], 2)
    put(api, SWITCH, {"enabled": False})
    if outcome in ("completed", "expired", "late", "gone"):
        post(api, f"attempts/{attempt['id']}/started", {"thread_id": "thread"}, token=burst["token"])
    if outcome in ("expired", "late"):
        expire(api, attempt["id"])
        if outcome == "expired":
            assert claim(api, base, "ops") is None  # Sweeps the overdue attempt.
            manager.step(hq, "ops", cloud_machines.now())
            assert placement(store) == (burst["runner_id"], 2)
            assert post(api, f"attempts/{attempt['id']}/renew", {}, burst["token"])["lease_seconds"] > 0
    if outcome == "gone":
        hq = cloud_machines.HQ("", "")
        monkeypatch.setattr(
            hq,
            "call",
            lambda *_: {"machines": [{"id": "_burst:1", "key": "_burst:1", "state": "failed", "last_error": "gone"}]},
        )
        manager.sync(hq, cloud_machines.now())
    elif outcome in ("revoke", "no_base"):
        with store.transaction() as c:
            c.execute("UPDATE assignments SET generation=3 WHERE bot='ops'")
            if outcome == "no_base":
                c.execute("UPDATE runners SET revoked_at=? WHERE id=?", (H.now(), base["runner_id"]))
        assert post(api, f"computers/{burst['runner_id']}/revoke", {})["revoked"] == (outcome == "revoke")
    else:
        path = f"attempts/{attempt['id']}/complete"
        body = {"outcome": "completed" if outcome in ("expired", "late") else outcome, "last_seq": 0}
        receipt = post(api, path, body, burst["token"], key="completion")
        assert post(api, path, body, burst["token"], key="completion") == receipt
    expected = (burst["runner_id"], 4) if outcome == "no_base" else (base["runner_id"], 4 if outcome == "revoke" else 3)
    assert placement(store) == expected
    with store.read() as c:
        assert bool(c.execute("SELECT 1 FROM burst_guests").fetchone()) == (outcome == "no_base")
        if outcome in ("expired", "late"):
            assert c.execute("SELECT state FROM jobs WHERE id=?", (attempt["job_id"],)).fetchone()[0] == "completed"
        if outcome == "no_base":
            assert c.execute("SELECT return_pending FROM burst_guests").fetchone()[0] == "revoke"
            assert c.execute("SELECT revoked_at FROM runners WHERE id=?", (burst["runner_id"],)).fetchone()[0] is None
        if outcome == "gone":
            assert cloud_machines.row(c, "_burst:1")["last_error"] == "gone"
            assert c.execute("SELECT revoked_at FROM runners WHERE id=?", (burst["runner_id"],)).fetchone()[0]
            assert c.execute("SELECT state FROM jobs WHERE id=?", (attempt["job_id"],)).fetchone()[0] == "uncertain"
            assert not c.execute(
                "SELECT 1 FROM registry_metadata WHERE key=?", (cloud_machines.REPLACES_KEY + "_burst:1",)
            ).fetchone()
    if outcome == "no_base":
        get(api, PREP, token=burst["token"], expected=401)
        with store.transaction() as c:
            code = cloud_machines.mint(c, "_shared", "ana")
        replacement = post(api, "runners/enroll", {"code": code, "label": "replacement", "platform": "test"})
        assert placement(store) == (replacement["runner_id"], 5)
        with store.read() as c:
            assert c.execute("SELECT revoked_at FROM runners WHERE id=?", (burst["runner_id"],)).fetchone()[0]
            assert not c.execute("SELECT 1 FROM burst_guests").fetchone()


@pytest.mark.parametrize("runner_state", ["silent_started", "lost_reply", "restarted"])
def test_silent_guest_returns_after_restore_window(api, fleet, monkeypatch, runner_state):
    store, base, burst = fleet
    queue_prepared(api, burst)
    attempt = claim(api, burst, "ops")
    aid, job = attempt["id"], attempt["job_id"]
    started = runner_state == "silent_started"
    if started:
        post(api, f"attempts/{aid}/started", {"thread_id": "burst-thread"}, burst["token"])
    expire(api, aid)
    scheduler = Scheduler(store, api.app.state.execution)
    scheduler.tick()
    assert placement(store) == (burst["runner_id"], 2)
    with store.read() as c:
        finished_at = c.execute("SELECT finished_at FROM attempts WHERE id=?", (aid,)).fetchone()[0]
    if runner_state == "restarted":
        # The restarted runner is online and polls without this attempt among its local work.
        heartbeat(api, burst)
        assert claim(api, burst, "ops") is None
    if started:
        post(api, f"jobs/{job}/retry", {"acknowledge_uncertain_effects": True}, expected=409)
    put(api, SWITCH, {"enabled": False})
    early = H.shift(finished_at, seconds=execution.STALL_MAX - 1)
    monkeypatch.setattr(H, "now", lambda: early)
    scheduler.tick()
    assert placement(store) == (burst["runner_id"], 2)
    deadline = H.shift(finished_at, seconds=execution.STALL_MAX)
    monkeypatch.setattr(H, "now", lambda: deadline)
    scheduler.tick()
    assert placement(store) == (base["runner_id"], 3)
    with store.read() as c:
        assert c.execute("SELECT state FROM jobs WHERE id=?", (job,)).fetchone()[0] == (
            "uncertain" if started else "queued"
        )
        assert bool(compute_pool.recovery_hold(c, aid)) == started
        assert not c.execute("SELECT 1 FROM burst_guests").fetchone()
    if started:
        post(api, f"attempts/{aid}/renew", {}, burst["token"], expected=409)
        assert claim(api, base, "ops") is None
    else:
        heartbeat(api, base)
        settled(api, base)
        resumed = claim(api, base, "ops")
        assert resumed and resumed["job_id"] == job


def test_routed_plan_cannot_claim_after_preparation(api, fleet):
    store, base, burst = fleet
    put(api, SWITCH, {"enabled": True})
    on(api, "ops", "claude", providers.recommended("anthropic", "claude"))
    heartbeat(api, base, runtime="claude")
    heartbeat(api, burst, runtime="claude")
    exit_runner = runner(api)
    subscription = add(api)
    pick(api, "ops", "claude", subscription["id"])
    put(api, "model-subscriptions/plan-exits/ana", {"runner_id": exit_runner["runner_id"]})
    queue(api)
    prepare(api, burst)
    with store.transaction() as c:
        plan_exits.set_enabled(c, True, "human:ana")
        assert plan_exits.routed(c, {"kind": "claude_token", "owner_id": "human:ana"}, require_ready=False)
    assert get(api, PREP, token=burst["token"]) == []
    assert claim(api, burst, "ops") is None
    assert placement(store) == (base["runner_id"], 1)


def test_managed_checkout_mismatch_returns_to_base_and_waits_for_worker_readiness(api, fleet, scaler):
    store, base, burst = fleet
    enable_scaler(api, store)
    queue(api)
    (entry,) = get(api, PREP, token=burst["token"])
    prepared = heartbeat(api, burst, entry, lease=True, revision="a" * 12)
    attempt = claim(api, burst, "ops")
    aid, job = attempt["id"], attempt["job_id"]
    post(
        api,
        f"attempts/{aid}/complete",
        {"outcome": "interrupted", "last_seq": 0, "burst_checkout": "mismatch"},
        burst["token"],
    )
    assert placement(store) == (base["runner_id"], 3)
    with store.read() as c:
        assert not compute_pool.recovery_hold(c, aid)
        assert execution.failed_starts(c, job) == 0
    heartbeat(api, base, runtime="pi", revision="b" * 12)
    assert get(api, PREP, token=burst["token"]) == []
    prepared["readiness"]["bots"]["ops"]["repository_revision"] = "c" * 12
    post(api, "runners/heartbeat", prepared, token=burst["token"])
    assert get(api, PREP, token=burst["token"])[0]["bot"] == "ops"
    assert claim(api, base, "ops")["job_id"] == job


@pytest.mark.parametrize("issue", ["unpublished", "quota", "fallback"])
def test_burst_holds_checkout_and_session_recovery_on_base(api, fleet, issue):
    store, base, burst = fleet
    queue_prepared(api, burst)
    attempt = claim(api, burst, "ops")
    aid, job = attempt["id"], attempt["job_id"]
    [assignment] = get(api, "runners/assignments", burst["token"])
    assert assignment["bot"] == "ops" and assignment["burst_guest"] is True
    body = {"outcome": "interrupted", "last_seq": 0}
    post(api, f"attempts/{aid}/started", {"thread_id": "primary"}, burst["token"])
    saved = {"thread_id": "fallback", "runtime": "codex", "harness": "codex", "model": "synthetic", "profile": ""}
    if issue in ("quota", "fallback"):
        body.update(outcome="failed", limited=True, last_seq=events(api, burst, attempt, [("tool", "Read")]))
        if issue == "fallback":
            body.update(fallback="codex", source_continuation=saved)
    else:
        body["burst_checkout"] = issue
    if issue == "unpublished":
        with store.transaction() as c:
            c.execute("UPDATE runners SET revoked_at=? WHERE id=?", (H.now(), base["runner_id"]))
    post(api, f"attempts/{aid}/complete", body, burst["token"])
    if issue == "unpublished":
        [assignment] = get(api, "runners/assignments", burst["token"])
        assert assignment["burst_guest"] is True
        with store.transaction() as c:
            assert compute_pool.recovery_hold(c, aid)
            c.execute("UPDATE runners SET revoked_at=NULL WHERE id=?", (base["runner_id"],))
            assert compute_pool.return_guests(c, burst["runner_id"])
    assert placement(store) == (base["runner_id"], 3)
    with store.transaction() as c:
        c.execute("UPDATE attempts SET finished_at=? WHERE id=?", (H.shift(H.now(), hours=-5), aid))
        api.app.state.execution.auto_reconcile(c)
        assert c.execute("SELECT state FROM jobs WHERE id=?", (job,)).fetchone()[0] == "uncertain"
        if issue == "fallback":
            assert execution.source_continuation(c, job)["thread_id"] == saved["thread_id"]
        if issue == "quota":
            assert execution.continuation_run(c, job)["thread_id"] == "primary"
    assert claim(api, base, "ops") is None
    path = f"jobs/{job}/retry"
    retry = {"acknowledge_uncertain_effects": True}
    fresh = {**retry, "fresh_session": True}
    if issue in ("quota", "fallback"):
        post(api, path, retry, expected=409)
        post(api, path, {**fresh, "acknowledge_uncertain_effects": False}, expected=409)
        post(api, path, fresh, token="cara-test", expected=403)
        assert post(api, path, fresh, key="fresh-session") == {"job_id": job, "state": "queued"}
        assert post(api, path, fresh, key="fresh-session")["state"] == "queued"
        assert get(api, PREP, token=burst["token"]) == []
        resumed = claim(api, base, "ops")
        assert resumed["job_id"] == job and resumed["fresh_session"] is True
        assert not resumed.get("continue_thread") and not resumed.get("source_continuation")
        assert resumed["recovery"]["resume_session"] is False
    else:
        post(api, path, fresh, expected=409)


def test_provision_during_borrow_uses_base(api, fleet):
    store, base, burst = fleet
    heartbeat(api, base, worktrees=True)
    queue_prepared(api, burst)
    assert claim(api, burst, "ops")
    with store.transaction() as c:
        task = H.task_create(c, "human:ana", "Work", "Provision a checkout", "bot:ops")
    link = post(api, f"tasks/{task['id']}/worktrees", {"repo": "acme/ops"})
    with store.read() as c:
        assert (
            c.execute("SELECT computer_id FROM task_links WHERE id=?", (link["link_id"],)).fetchone()[0]
            == base["runner_id"]
        )


@pytest.mark.parametrize("phase", ["_offer", "start_claimed"])
def test_burst_failure_leaves_base_work_claimable(api, fleet, phase, monkeypatch):
    _, base, burst = fleet
    queue_prepared(api, burst)
    owner = compute_pool if phase == "_offer" else api.app.state.execution
    original = getattr(owner, phase)

    def fail(*args):
        if phase == "_offer" or isinstance(args[-1], dict) and args[-1].get("burst"):
            raise RuntimeError("Burst check failed")
        return original(*args)

    monkeypatch.setattr(owner, phase, fail)
    assert claim(api, burst, "ops") is None
    assert claim(api, base, "ops")


@pytest.mark.parametrize("changed", ["generation", "repository", "harness", "runner"])
def test_preparation_is_bound_to_the_reported_checkout(api, fleet, changed):
    store, base, burst = fleet
    entry = queue_prepared(api, burst)
    if changed == "generation":
        assign(api, base, "ops", generation=1)
    elif changed == "repository":
        with store.transaction() as c:
            c.execute("UPDATE bot_config SET repo='acme/renamed' WHERE bot='ops'")
    elif changed == "harness":
        heartbeat(api, burst, {**entry, "config": {**entry["config"], "harness": "pi"}})
    else:
        burst = machine(api, "_burst:2")
        heartbeat(api, burst, entry)
    assert claim(api, burst, "ops") is None
    assert placement(store)[0] == base["runner_id"]


@pytest.mark.parametrize(
    "refusal", [cloud_machines.Refused("daily_budget", 429), cloud_machines.Refused("unreachable")]
)
def test_renewal_fences_new_guests_until_hq_confirms(api, fleet, refusal, monkeypatch):
    store, _, burst = fleet
    queue_prepared(api, burst)
    manager, hq = api.app.state.cloud_machines, FakeHQ()
    at = cloud_machines.now() + cloud_machines.HEARTBEAT + 1
    monkeypatch.setattr(cloud_machines, "now", lambda: at)
    hq.fail["heartbeat"] = [refusal]
    assert manager.step(hq, "_burst:1", at) == "heartbeat"
    assert claim(api, burst, "ops") is None
    with store.read() as c:
        row = cloud_machines.row(c, "_burst:1")
        assert row["heartbeat_at"] + row["ttl_s"] > at
        at = row["next_try_at"]
    assert manager.step(hq, "_burst:1", at) == "heartbeat"
    assert claim(api, burst, "ops")


def test_claim_needs_start_and_drain_time_on_confirmed_lease(api, fleet, monkeypatch):
    store, _, burst = fleet
    queue_prepared(api, burst)
    with store.read() as c:
        row = cloud_machines.row(c, "_burst:1")
    boundary = row["heartbeat_at"] + row["ttl_s"] - start_places.START_SECONDS - cloud_machines.BURST_DRAIN_SECONDS
    monkeypatch.setattr(cloud_machines, "now", lambda: boundary + 1)
    assert claim(api, burst, "ops") is None
    monkeypatch.setattr(cloud_machines, "now", lambda: boundary)
    assert claim(api, burst, "ops")


def test_preparation_uses_the_next_eligible_job_and_idle_slot(api, fleet):
    store, _, burst = fleet
    second, tenth = machine(api, "_burst:2"), machine(api, "_burst:10")
    put(api, SWITCH, {"enabled": True})
    first = queue(api)
    before = prepare(api, burst)
    queue(api, "Next")
    with store.transaction() as c:
        job = c.execute("SELECT id FROM jobs WHERE message_id=?", (first["id"],)).fetchone()[0]
        bot_stops.hold_job(c, store.settings, job, "RuntimeError")
    assert get(api, PREP, token=burst["token"])[0]["burst_preparation"] == before["burst_preparation"]
    heartbeat(api, burst, capable=False)
    assert get(api, PREP, token=tenth["token"]) == []
    assert claim(api, second, "ops") is None
    prepare(api, second)
    assert claim(api, second, "ops")["job_id"] != job


@pytest.mark.parametrize("signed_in", [True, False])
def test_preparation_matches_runner_manifests_and_gateway_switch(api, fleet, signed_in, monkeypatch):
    from backend.tests.helpers.models_preset import choose, on_claude

    monkeypatch.setenv("TICO_GATEWAY_KEY", "synthetic-test-key")

    store, base, burst = fleet
    choose(api, models_preset.RECOMMENDED)
    on_claude(api, "ops")
    with store.transaction() as c:
        c.execute("UPDATE bots SET slug='operator' WHERE id='ops'")
    heartbeat(api, base, runtime="codex", signed_in=False)
    with store.transaction() as c:
        c.execute(
            "UPDATE runner_states SET readiness=json_remove(readiness,'$.runtimes') WHERE runner_id=?",
            (base["runner_id"],),
        )
    heartbeat(api, burst, runtime="claude", signed_in=signed_in)
    put(api, SWITCH, {"enabled": True})
    queue(api)
    (entry,) = get(api, PREP, token=burst["token"])
    assert entry["config"]["name"] == "operator"
    heartbeat(api, burst, entry, signed_in=signed_in)
    assert claim(api, burst, "ops")["config"]["runtime"] == ("claude" if signed_in else "pi")


@pytest.fixture
def scaler(api, fleet, monkeypatch):
    monkeypatch.setenv("TICO_GATEWAY_KEY", "synthetic-test-key")
    store, base, burst = fleet
    hq = FakeHQ()
    hq.caps["daily_budget_usd"] = 1.25
    hq.sizes = [{**size, "cpu": 8} for size in SIZES if size["name"] == "large"]
    worker = hq.create("_burst:1", "large", "synthetic", "https://acme.example")
    hq._machine(worker["id"], state="running")
    hq.calls.clear()
    clock = [cloud_machines.now()]
    monkeypatch.setattr(cloud_machines, "now", lambda: clock[0])
    with store.transaction() as c:
        BS.write(c, "ops", models_preset.on_gateway(BS.read(c, "ops")))
        cloud_machines.save(
            c, "_burst:1", machine_id=worker["id"], machine_size="large", size="large", active_at=clock[0]
        )
    heartbeat(api, base, runtime="pi")
    heartbeat(api, burst, runtime="pi", lease=True)
    return api.app.state.cloud_machines, hq, clock


def offer_budget(store):
    with store.transaction() as c:
        c.execute(
            "INSERT INTO registry_metadata VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (
                cloud_machines.SIZES_KEY,
                encode({"sizes": [{"name": "large", "cpu": 8}], "caps": {"daily_budget_usd": 1.25}}),
            ),
        )


def enable_scaler(api, store):
    offer_budget(store)
    put(api, SWITCH, {"enabled": True})
    assert put(api, AUTOSCALE, {"enabled": True}) == {"enabled": True}
    with store.transaction() as c:
        assert not cloud_machines.row(c, "_burst:1")["burst_managed"]
        cloud_machines.save(c, "_burst:1", burst_managed=1)


def saturate_base(api, store, base):
    on(api, "finance", "codex", providers.recommended("openai", "codex"))
    assign(api, base, "finance")
    report = heartbeat(api, base, runtime="pi")["readiness"]["bots"]["ops"]
    heartbeat(api, base, runtime="pi", bots={"ops": report, "finance": {**report, "runtime": "codex"}})
    with store.transaction() as c:
        c.execute("UPDATE runners SET turns_at_once=1 WHERE id=?", (base["runner_id"],))
    post(api, "chat/finance", {"text": "Occupy the Base slot"})
    turn = claim(api, base, "finance")
    assert turn
    post(api, f"attempts/{turn['id']}/started", {"thread_id": "base"}, base["token"])


def test_autoscale_defaults_off_requires_admin_rehome_and_hq_budget(api, fleet, scaler):
    store, _, burst = fleet
    manager, hq, clock = scaler
    assert get(api, AUTOSCALE) == {"enabled": False}
    get(api, AUTOSCALE, "cara-test", expected=403)
    put(api, AUTOSCALE, {"enabled": True}, "cara-test", expected=403)
    put(api, AUTOSCALE, {"enabled": True}, expected=409)
    put(api, SWITCH, {"enabled": True})
    assert put(api, AUTOSCALE, {"enabled": True}, expected=409)["error"]["code"] == "daily_budget_required"
    manager.scale_burst(hq, clock[0])
    clock[0] += 3600
    manager.scale_burst(hq, clock[0])
    with store.read() as c:
        assert cloud_machines.burst_lease(c, burst["runner_id"]) is None
        assert cloud_machines.row(c, "_burst:1")["drain_started_at"] is None
    assert hq.calls == []


def test_autoscale_waits_for_saturation_prefers_parked_and_backs_off_for_hq_cap(api, fleet, scaler):
    store, base, _ = fleet
    manager, hq, clock = scaler
    enable_scaler(api, store)
    worker = next(iter(hq.machines))
    with store.transaction() as c:
        cloud_machines.save(c, "_burst:1", state="stopped")
    hq._machine(worker, state="stopped")
    manager.sync(hq, clock[0])
    hq.calls.clear()
    queue(api)
    manager.scale_burst(hq, clock[0])
    clock[0] += 31
    manager.scale_burst(hq, clock[0])
    assert not hq.calls  # Base can take this turn.
    with store.transaction() as c:
        cloud_machines.save(c, "_shared", state="stopped")
    manager.scale_burst(hq, clock[0])
    clock[0] += 31
    manager.scale_burst(hq, clock[0])
    with store.transaction() as c:
        cloud_machines.save(c, "_shared", state="starting", start_asked_at=clock[0])
    clock[0] += 31
    manager.scale_burst(hq, clock[0])
    assert not hq.calls
    with store.transaction() as c:
        cloud_machines.save(c, "_shared", state="running", start_asked_at=None)
    saturate_base(api, store, base)
    manager.scale_burst(hq, clock[0])
    clock[0] += 29
    manager.scale_burst(hq, clock[0])
    assert not hq.calls
    hq.fail["start"] = [cloud_machines.Refused("daily_budget", 429)]
    clock[0] += 1
    manager.sync(hq, clock[0])
    manager.scale_burst(hq, clock[0])
    assert [call for call in hq.calls if call[0] == "start"] == [("start", worker, None)]
    clock[0] += cloud_machines.FROZEN_RETRY - 1
    manager.scale_burst(hq, clock[0])
    assert hq.actions() == ["start"]
    clock[0] += 1
    manager.sync(hq, clock[0])
    manager.scale_burst(hq, clock[0])
    clock[0] += 31
    manager.scale_burst(hq, clock[0])
    assert [call for call in hq.calls if call[0] == "start"] == [
        ("start", worker, None)
    ] * 2  # The second launch remains unresolved.
    assert len({launch["request_id"] for launch in hq.launch_options}) == 2


def test_autoscale_avoids_hq_keys_and_keeps_a_lost_create_intent(api, fleet, scaler):
    store, base, _ = fleet
    manager, hq, clock = scaler
    enable_scaler(api, store)
    with store.transaction() as c:
        c.execute("DELETE FROM cloud_machines WHERE bot='_burst:1'")
    saturate_base(api, store, base)
    queue(api)
    manager.scale_burst(hq, clock[0])
    clock[0] += 30
    manager.sync(hq, clock[0])
    hq.fail["create"] = [cloud_machines.Refused("unreachable")]
    manager.scale_burst(hq, clock[0])
    assert [(call[0], call[1], call[2]) for call in hq.calls if call[0] == "create"] == [
        ("create", "_burst:2", "large")
    ]
    [options] = hq.launch_options
    assert options["managed"] is True and len(options["request_id"]) <= 64
    clock[0] += 31
    manager.sync(hq, clock[0])
    manager.scale_burst(hq, clock[0])
    assert hq.actions() == ["create"]


def test_lost_managed_create_adopts_only_an_hq_managed_machine(api, fleet, scaler, monkeypatch):
    store, _, _ = fleet
    manager, fake, clock = scaler
    enable_scaler(api, store)
    key = "_burst:1"
    with store.transaction() as c:
        cloud_machines.save(c, key, machine_id=None, runner_id=None, create_asked_at=clock[0])
    (mid,) = fake.machines
    hq = cloud_machines.HQ("synthetic", "https://acme.example")
    monkeypatch.setattr(hq, "call", lambda _method: fake.list())

    manager.sync(hq, clock[0])
    fake._machine(mid, managed=False)
    manager.sync(hq, clock[0])
    with store.read() as c:
        current = cloud_machines.row(c, key)
        assert current["machine_id"] is None
        assert current["create_asked_at"] == clock[0]

    fake._machine(mid, managed=True)
    manager.sync(hq, clock[0])
    with store.read() as c:
        assert cloud_machines.row(c, key)["machine_id"] == mid


def test_autoscale_fences_an_in_flight_resume_through_opt_out(api, fleet, scaler):
    store, _, burst = fleet
    manager, hq, clock = scaler
    enable_scaler(api, store)
    worker = next(iter(hq.machines))
    with store.transaction() as c:
        cloud_machines.save(c, "_burst:1", state="stopped", start_asked_at=clock[0])
    hq._machine(worker, state="stopped", pending="start")
    put(api, AUTOSCALE, {"enabled": False})
    manager.sync(hq, clock[0])
    with store.read() as c:
        current = cloud_machines.row(c, "_burst:1")
        assert current["burst_managed"] and current["drain_started_at"] is not None
    hq._machine(worker, state="running", pending=None)
    manager.sync(hq, clock[0])
    with store.read() as c:
        assert cloud_machines.burst_lease(c, burst["runner_id"])["draining"]
    assert manager.step(hq, "_burst:1", clock[0]) == "stop"
    with store.read() as c:
        current = cloud_machines.row(c, "_burst:1")
        assert current["burst_managed"] and current["drain_started_at"] is None


def test_autoscale_backs_off_company_after_never_enrolled_worker(api, fleet, scaler):
    store, base, _ = fleet
    manager, hq, clock = scaler
    enable_scaler(api, store)
    worker = next(iter(hq.machines))
    with store.transaction() as c:
        cloud_machines.save(c, "_burst:1", enroll_by=clock[0], runner_id=None, burst_launch_at=clock[0] - 960)
    assert manager.step(hq, "_burst:1", clock[0]) == "delete"
    with store.read() as c:
        assert (
            float(
                c.execute("SELECT value FROM registry_metadata WHERE key=?", (cloud_machines.BURST_NEXT,)).fetchone()[0]
            )
            == clock[0] + 3600
        )
        assert c.execute("SELECT 1 FROM events WHERE action='cloud_machine.burst_launch_failed'").fetchone()
    saturate_base(api, store, base)
    queue(api)
    manager.scale_burst(hq, clock[0])
    clock[0] += 3599
    manager.scale_burst(hq, clock[0])
    assert hq.actions() == ["delete"]
    assert hq.machines[worker]["state"] == "deleted"


def test_launch_failures_stop_scaling_across_keys_until_reenabled(api, fleet, scaler):
    store, base, _ = fleet
    manager, hq, clock = scaler
    enable_scaler(api, store)
    saturate_base(api, store, base)
    queue(api)
    old = next(iter(hq.machines))
    hq._machine(old, state="failed")
    manager.sync(hq, clock[0])
    manager.scale_burst(hq, clock[0])
    clock[0] += cloud_machines.BURST_WAIT
    for count in range(1, 4):
        manager.sync(hq, clock[0])
        manager.scale_burst(hq, clock[0])
        mid = next(reversed(hq.machines))
        assert hq.machines[mid]["key"] == f"_burst:{count + 1}"
        hq._machine(mid, state="stopped" if count == 2 else "failed")
        manager.sync(hq, clock[0])
        manager.sync(hq, clock[0])
        with store.read() as c:
            assert cloud_machines.burst_failures(c) == count
            assert c.execute(
                "SELECT count(*) FROM messages WHERE kind='notice' AND json_extract(refs,'$.cloud_machine')=?",
                (hq.machines[mid]["key"],),
            ).fetchone()[0] == int(count == 3)
        clock[0] += cloud_machines.BURST_FAILURE_WAIT
    manager.sync(hq, clock[0])
    manager.scale_burst(hq, clock[0])
    put(api, AUTOSCALE, {"enabled": True})
    manager.scale_burst(hq, clock[0])
    assert hq.actions() == ["create"] * 3
    put(api, AUTOSCALE, {"enabled": False})
    put(api, AUTOSCALE, {"enabled": True})
    manager.scale_burst(hq, clock[0])
    assert hq.actions() == ["create"] * 4


def test_ready_worker_resets_launch_failures(api, fleet, scaler):
    store, _, _ = fleet
    manager, hq, clock = scaler
    enable_scaler(api, store)
    with store.transaction() as c:
        cloud_machines.save(c, "_burst:1", burst_launch_at=clock[0])
        c.execute("INSERT INTO registry_metadata VALUES(?, '2')", (cloud_machines.BURST_FAILURES,))
    manager.step(hq, "_burst:1", clock[0])
    with store.read() as c:
        assert cloud_machines.burst_failures(c) == 0
        assert cloud_machines.row(c, "_burst:1")["burst_launch_at"] is None


def test_failed_parked_disk_is_replaced_without_adopting_a_pilot(api, fleet, scaler):
    store, base, _ = fleet
    manager, hq, clock = scaler
    offer_budget(store)
    put(api, SWITCH, {"enabled": True})
    put(api, AUTOSCALE, {"enabled": True})
    pilot = next(iter(hq.machines))
    hq._machine(pilot, state="stopped")
    parked = hq.create("_burst:2", "large", "synthetic", "https://acme.example")["id"]
    machine(api, "_burst:2")
    with store.transaction() as c:
        cloud_machines.save(c, "_burst:2", machine_id=parked, state="stopped", burst_managed=1)
    hq._machine(parked, state="failed")
    manager.sync(hq, clock[0])
    with store.read() as c:
        assert cloud_machines.row(c, "_burst:2")["runner_id"] is None
        assert cloud_machines.burst_failures(c) == 0
    hq.calls.clear()
    saturate_base(api, store, base)
    queue(api)
    manager.scale_burst(hq, clock[0])
    clock[0] += cloud_machines.BURST_WAIT
    manager.scale_burst(hq, clock[0])
    assert hq.actions() == ["create"]
    assert hq.calls[-1][1] == "_burst:3"


def test_autoscale_stops_a_worker_that_never_confirms_lease_support(api, fleet, scaler):
    store, _, burst = fleet
    manager, hq, clock = scaler
    enable_scaler(api, store)
    heartbeat(api, burst, runtime="pi")
    with store.transaction() as c:
        cloud_machines.save(c, "_burst:1", burst_launch_at=clock[0] - cloud_machines.CREATE_HOLD - 1, ttl_s=None)
    assert manager.step(hq, "_burst:1", clock[0]) is None
    with store.read() as c:
        assert cloud_machines.row(c, "_burst:1")["drain_started_at"] == clock[0]
        assert (
            float(
                c.execute("SELECT value FROM registry_metadata WHERE key=?", (cloud_machines.BURST_NEXT,)).fetchone()[0]
            )
            == clock[0] + 3600
        )
    assert manager.step(hq, "_burst:1", clock[0]) == "stop"
    assert hq.actions() == ["stop"]


def test_autoscale_parks_a_ready_worker_after_it_loses_lease_support(api, fleet, scaler):
    store, _, burst = fleet
    manager, hq, clock = scaler
    enable_scaler(api, store)
    heartbeat(api, burst, runtime="pi", lease=False)
    with store.read() as c:
        worker = cloud_machines.row(c, "_burst:1")
        assert worker["burst_launch_at"] is None
        clock[0] += cloud_machines.pause_after(worker)
    with store.transaction() as c:
        cloud_machines.save(c, "_burst:1", heartbeat_at=clock[0])

    manager.scale_burst(hq, clock[0])
    assert manager.step(hq, "_burst:1", clock[0]) == "stop"
    assert hq.actions() == ["stop"]


def test_autoscale_parks_with_a_settled_guest_waiting_for_base(api, fleet, scaler):
    store, base, burst = fleet
    manager, hq, clock = scaler
    enable_scaler(api, store)
    queue(api)
    (entry,) = get(api, PREP, burst["token"])
    heartbeat(api, burst, entry, lease=True)
    attempt = claim(api, burst, "ops")
    aid = attempt["id"]
    post(api, f"attempts/{aid}/started", {"thread_id": "burst"}, burst["token"])
    with store.transaction() as c:
        c.execute("UPDATE runners SET revoked_at=? WHERE id=?", (H.now(), base["runner_id"]))
    post(api, f"attempts/{aid}/complete", {"outcome": "completed", "last_seq": 0}, burst["token"])

    with store.read() as c:
        guest = dict(c.execute("SELECT * FROM burst_guests WHERE bot='ops'").fetchone())
        assert guest["return_pending"] == "settled"
        worker = cloud_machines.row(c, "_burst:1")
        finished = c.execute("SELECT finished_at FROM attempts WHERE id=?", (aid,)).fetchone()[0]
        clock[0] = max(clock[0], cloud_machines.epoch(finished)) + cloud_machines.pause_after(worker)
    manager.scale_burst(hq, clock[0])
    assert manager.step(hq, "_burst:1", clock[0]) == "stop"
    with store.read() as c:
        assert dict(c.execute("SELECT * FROM burst_guests WHERE bot='ops'").fetchone()) == guest
    assert placement(store) == (burst["runner_id"], attempt["generation"])


def test_autoscale_parks_after_idle_window_and_wakes_only_the_selected_runner(api, fleet, scaler):
    store, _, burst = fleet
    manager, hq, clock = scaler
    enable_scaler(api, store)
    other = machine(api, "_burst:2")
    with store.transaction() as c:
        cloud_machines.save(c, "_burst:2", burst_managed=1, active_at=clock[0] - 100)
    heartbeat(api, other, runtime="pi", lease=True)
    queue(api)
    with store.read() as c:
        since = c.execute("SELECT max(seq) FROM changes").fetchone()[0]
    manager.scale_burst(hq, clock[0])
    manager.scale_burst(hq, clock[0])
    with store.read() as c:
        hints = c.execute(
            "SELECT subject_id FROM changes WHERE seq>? AND topic='runner' AND kind='assignments'", (since,)
        )
        assert [row[0] for row in hints] == [burst["runner_id"]]
        assert cloud_machines.row(c, "_burst:2")["active_at"] == clock[0] - 100
    assert get(api, PREP, burst["token"])[0]["bot"] == "ops"
    # A preparation hint can stay outstanding when the checkout cannot take the turn.
    clock[0] += 1799
    with store.transaction() as c:
        cloud_machines.save(c, "_burst:1", heartbeat_at=clock[0])
        cloud_machines.save(c, "_burst:2", heartbeat_at=clock[0])
    manager.scale_burst(hq, clock[0])
    with store.read() as c:
        assert cloud_machines.row(c, "_burst:1")["drain_started_at"] is None
    clock[0] += 1
    manager.scale_burst(hq, clock[0])
    with store.read() as c:
        assert cloud_machines.row(c, "_burst:1")["drain_started_at"] == clock[0]
    assert hq.calls == []
    assert manager.step(hq, "_burst:1", clock[0]) == "stop"
    assert [call[0] for call in hq.calls] == ["stop"]


@pytest.mark.parametrize("start_first", [False, True])
def test_autoscale_drain_serializes_with_started_and_guest_return(api, fleet, scaler, start_first):
    store, base, burst = fleet
    manager, hq, clock = scaler
    enable_scaler(api, store)
    queue(api)
    (entry,) = get(api, PREP, burst["token"])
    heartbeat(api, burst, entry, lease=True)
    answer = post(api, "jobs/claim", {"bot": "ops", "start_places": True}, burst["token"])
    attempt = answer["attempt"]
    assert attempt["generation"] == 2 and answer["burst_lease"]["run_seconds"] > 0
    aid = attempt["id"]
    path = f"attempts/{aid}/started"
    body = {"thread_id": "burst"}
    if start_first:
        post(api, path, body, burst["token"], key="start-race")
    put(api, AUTOSCALE, {"enabled": False})
    assert manager.step(hq, "_burst:1", clock[0]) is None
    if start_first:
        assert post(api, path, body, burst["token"], key="start-race")["started"]
        post(api, f"attempts/{aid}/complete", {"outcome": "completed", "last_seq": 0}, burst["token"])
    else:
        assert post(api, path, body, burst["token"], expected=409)["error"]["code"] == "burst_draining"
        assert claim(api, base, "ops") is None
        post(
            api,
            f"attempts/{aid}/events",
            {"events": [{"seq": 1, "kind": "status", "payload": {"yielded": "Draining", "planned_drain": True}}]},
            burst["token"],
        )
        post(
            api, f"attempts/{aid}/complete", {"outcome": "interrupted", "last_seq": 1, "yielded": True}, burst["token"]
        )
        returned = claim(api, base, "ops")
        assert returned["generation"] == 3 and returned["job_id"] == attempt["job_id"]
        post(api, f"attempts/{returned['id']}/started", {"thread_id": "base"}, base["token"])
    assert manager.step(hq, "_burst:1", clock[0]) == "stop"
    with store.read() as c:
        started = c.execute(
            "SELECT generation FROM attempts WHERE job_id=? AND started_at IS NOT NULL", (attempt["job_id"],)
        )
        assert [row[0] for row in started] == [2 if start_first else 3]


def test_autoscale_lost_renewal_cannot_extend_the_reported_cutoff(api, fleet, scaler):
    store, _, burst = fleet
    manager, hq, clock = scaler
    enable_scaler(api, store)
    body = heartbeat(api, burst, runtime="pi", lease=True)
    path = "runners/heartbeat"

    def lease():
        return post(api, path, body, burst["token"], key="same-heartbeat")["burst_lease"]["run_seconds"]

    first = lease()
    clock[0] += cloud_machines.HEARTBEAT + 1
    hq.fail["heartbeat"] = [cloud_machines.Refused("unreachable")]
    assert manager.step(hq, "_burst:1", clock[0]) == "heartbeat"
    assert lease() == first - cloud_machines.HEARTBEAT - 1
    with store.read() as c:
        assert cloud_machines.held(c, burst["runner_id"])


@pytest.mark.parametrize("ttl", [0, 30])
def test_shorter_renewal_preserves_cutoff_until_it_runs_low(api, fleet, scaler, monkeypatch, ttl):
    store, _, burst = fleet
    manager, hq, clock = scaler
    enable_scaler(api, store)
    with store.transaction() as c:
        cloud_machines.save(c, "_burst:1", ttl_s=600, heartbeat_at=clock[0])
    queue(api)
    (entry,) = get(api, PREP, burst["token"])
    prepare_body = heartbeat(api, burst, entry, lease=True)
    original = int(clock[0] * 1000)
    clock[0] += cloud_machines.HEARTBEAT + 1
    monkeypatch.setattr(hq, "heartbeat", lambda _: ttl)
    assert manager.step(hq, "_burst:1", clock[0]) == "heartbeat"
    report = post(api, "runners/heartbeat", prepare_body, burst["token"])
    assert report["burst_lease"] == {"confirmed_at_ms": original, "run_seconds": 479, "draining": False}
    clock[0] += 300
    assert manager.step(hq, "_burst:1", clock[0]) == "heartbeat"
    assert claim(api, burst, "ops") is None
    report = post(api, "runners/heartbeat", prepare_body, burst["token"])
    assert report["burst_lease"] == {
        "confirmed_at_ms": original,
        "run_seconds": 179,
        "draining": True,
    }
