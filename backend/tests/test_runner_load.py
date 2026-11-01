"""Runner minute load, durable per-attempt snapshots and plain-bot observability (synthetic fixtures only)."""
import json
import time

from backend import models as M, performance as P
from backend.tests.test_api import api, get, headers, post, setup_attempt  # noqa: F401
from backend.tests.test_performance import sample
from backend.tests.test_bot_access import world  # noqa: F401


def point(at, cpu=95, mem=.5, active=3, load=None):
    return sample(at, cpu=cpu, mem=mem) | {"load": [load if load is not None else 1, 1, 1], "active": active,
                                  "swap_used": 20, "swap_total": 100}


def test_sampler_keeps_one_load_row_per_minute_and_prunes_at_48_hours(api):
    r, _, _ = setup_attempt(api)
    now = (int(time.time()) // 60 - 1) * 60
    rid = r["runner_id"]
    with api.app.state.store.transaction() as c:
        P.ingest(c, rid, [M.PerformanceSample(**point(now - 49 * 3600))], now=now - 49 * 3600)
    samples = [point(now, cpu=85), point(now + 10, cpu=97.5)]
    post(api, "runners/performance", {"samples": samples}, token=r["token"])
    # A delayed retry of the older reading cannot overwrite the newest reading in its minute.
    post(api, "runners/performance", {"samples": [point(now, cpu=5, active=1)]}, token=r["token"])
    with api.app.state.store.read() as c:
        rows = c.execute("SELECT * FROM runner_load_samples WHERE runner_id=?", (rid,)).fetchall()
    assert len(rows) == 1
    assert dict(rows[0]) == {"runner_id": rid, "created_at": now, "sampled_at": now + 10,
                                "cpu_pct": 97.5, "mem_used": 500, "mem_total": 1000,
                                "swap_used": 20, "swap_total": 100, "running_turns": 3}


def test_attempt_stores_fresh_start_and_end_snapshots_visible_in_run_list(api):
    r, _, a = setup_attempt(api)
    features = get(api, "runners/desired", token=r["token"])["features"]
    assert all(features[name] is True for name in ("load_snapshot", "run_rotation", "run_session", "run_diagnostics", "attempt_thread"))
    now = time.time()
    start = {"at": now, "cpu_pct": 125.0, "mem_pct": 55.0, "running_turns": 2}
    end = {"at": now + 1, "cpu_pct": 25.0, "mem_pct": 45.0, "running_turns": 1}
    post(api, f"attempts/{a['id']}/started", {"thread_id": "load-test", "load_snapshot": start}, token=r["token"])
    body = {"outcome": "completed", "text": "Finished", "last_seq": 0, "load_snapshot": end,
            "usage": {"input_tokens": 100, "cached_tokens": 0, "output_tokens": 20,
                      "model": "gpt-6-sol", "runtime": "codex", "billing": "api"}}
    post(api, f"attempts/{a['id']}/complete", body, token=r["token"])
    run = next(x for x in get(api, "bots/ops/turns") if x["id"] == a["id"])
    assert run["load_start"] == start and run["load_end"] == end
    summary = next(x for x in api.get("/api/runs", headers=headers()).json() if x["id"] == a["id"])
    assert summary["load_start"] == start and summary["load_end"] == end
    assert summary["output_tokens"] == 20 and summary["cost_usd"] == run["est_cost_usd"] > 0
    assert run["end_reason"] == "Finished" and run["ended_at"]
    # Snapshots outlive the rolling 48h samples, and Delete history removes both.
    P.sweep(api.app.state.store, int(now + 49 * 3600))
    assert next(x for x in get(api, "bots/ops/turns") if x["id"] == a["id"])["load_start"] == start
    assert api.delete(f"/api/v2/computers/{r['runner_id']}/performance", headers=headers()).status_code == 200
    assert next(x for x in get(api, "bots/ops/turns") if x["id"] == a["id"])["load_start"] is None


def test_plain_bot_lists_closed_computer_with_latest_numbers_without_change_rights(world):
    from backend.tests.test_mcp import call
    api, tokens, _ = world
    r, _, _ = setup_attempt(api)
    rid = r["runner_id"]
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE runners SET accepts_member_bots=0 WHERE id=?", (rid,))
    post(api, "runners/performance", {"samples": [point(time.time(), cpu=75)]}, token=r["token"])
    bot_token = tokens["stranger"]
    failed, seen = call(api, "hub_computer_list", {}, token=bot_token)
    assert not failed, seen
    computer = next(x for x in seen["computers"] if x["id"] == rid)
    assert computer["latest_sample"]["cpu_pct"] == 75
    assert computer["latest_sample"]["mem_pct"] == 50
    assert computer["latest_sample"]["running_turns"] == 3 and computer["overloaded"] is False
    post(api, f"computers/{rid}/performance", {"mode": "off"}, token=bot_token, expected=403)


def test_five_full_minutes_trip_overload_in_health_and_gaps_calm_stale_or_off_clear_it(api):
    r, _, a = setup_attempt(api)
    rid = r["runner_id"]
    now = int(time.time()) // 60 * 60
    with api.app.state.store.transaction() as c:
        P.ingest(c, rid, [M.PerformanceSample(**point(now - 300 + i * 60)) for i in range(5)], now=now)
    assert next(x for x in get(api, "computers")["computers"] if x["id"] == rid).get("overloaded") is not True
    post(api, "runners/performance", {"samples": [point(now)]}, token=r["token"])
    assert next(x for x in get(api, "computers")["computers"] if x["id"] == rid).get("overloaded") is True
    check = next(x for x in get(api, "health", token=a["token"])["checks"] if x["id"] == "runner_load:" + rid)
    assert check["status"] == "warn" and "five minutes" in check["summary"]
    with api.app.state.store.transaction() as c:
        assert P.load_status(c, rid, now + 121)["overloaded"] is False
        c.execute("DELETE FROM runner_load_samples WHERE runner_id=? AND created_at=?", (rid, now - 120))
        assert P.load_status(c, rid, now)["overloaded"] is False
        P.ingest(c, rid, [M.PerformanceSample(**point(now - 120, cpu=90, mem=.9))], now=now)
        assert P.load_status(c, rid, now)["overloaded"] is False  # strictly above 90
        P.ingest(c, rid, [M.PerformanceSample(**point(now - 120, cpu=0, mem=.95))], now=now)
        assert P.load_status(c, rid, now)["overloaded"] is True  # memory alone qualifies
    post(api, f"computers/{rid}/performance", {"mode": "off"})
    with api.app.state.store.read() as c:
        assert P.load_status(c, rid, now) == {"latest_sample": None, "overloaded": False}



def test_high_load_average_with_cpu_to_spare_does_not_hold_starts(api):
    # Many turns waiting on the network push load average far past the cores while CPU still has room.
    r, _, _ = setup_attempt(api)
    rid = r["runner_id"]
    now = int(time.time()) // 60 * 60
    with api.app.state.store.transaction() as c:
        P.ingest(c, rid, [M.PerformanceSample(**point(now - 300 + i * 60, cpu=70, mem=.3, load=14)) for i in range(6)],
                 now=now)
        status = P.load_status(c, rid, now)
    assert status["overloaded"] is False and status["latest_sample"]["cpu_pct"] == 70

def test_new_bot_computer_visibility_keeps_hidden_bots_and_accounts_private(world):
    from backend.repositories import save_metadata
    from backend.store import H
    from backend.tests.test_bot_access import set_access
    from backend.tests.test_mcp import call
    api, tokens, _ = world
    r, _, _ = setup_attempt(api)
    rid = r["runner_id"]
    set_access(api, see={"teams": ["legal"]}, read={"teams": ["legal"]}, write={"teams": ["legal"]})
    with api.app.state.store.transaction() as c:
        counsel_id = H.resolve_bot(c, "counsel")["id"]
        c.execute("UPDATE runners SET accepts_member_bots=0,readiness=? WHERE id=?",
                  (json.dumps({"schema_version": 1, "recent_errors": ["private runner log"], "bots": {}}), rid))
        save_metadata(c, "computer-repositories:" + rid,
                      {"repositories": [{"full_name": "acme/private-repo", "error": "private clone error"}]})
        c.execute("INSERT INTO assignments(bot,runner_id,generation,updated_at,updated_by) VALUES(?,?,1,?,'human:ana')",
                  (counsel_id, rid, P._iso(time.time())))
    failed, detail = call(api, "hub_computer_list", {"computer": rid}, token=tokens["stranger"])
    assert not failed, detail
    assert "counsel" not in str(detail) and counsel_id not in str(detail)
    assert detail["id"] == rid
    assert "repositories" not in detail and "recent_errors" not in detail.get("readiness", {})
    assert "operator" not in detail
    raw = next(x for x in get(api, "computers", token=tokens["stranger"])["computers"] if x["id"] == rid)
    assert set(raw) == {"id", "label", "online", "platform", "accepts_member_bots", "latest_sample", "overloaded", "turn_counts", "turn_summary"}
    for section in ("repositories", "recent_errors"):
        failed, page = call(api, "hub_computer_list", {"computer": rid, "section": section}, token=tokens["stranger"])
        assert not failed and page["items"] == []
    owner = next(x for x in get(api, "computers")["computers"] if x["id"] == rid)
    assert owner["repositories"][0]["full_name"] == "acme/private-repo"
    assert owner["readiness"]["recent_errors"] == ["private runner log"]
    # Full details stay available for computers already readable before load discovery.
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE runners SET accepts_member_bots=1 WHERE id=?", (rid,))
    failed, opened = call(api, "hub_computer_list", {"computer": rid}, token=tokens["stranger"])
    assert not failed and opened["repositories"] == owner["repositories"]


def test_raw_mac_swap_is_visible_without_changing_diagnostic_overloads(api):
    r, _, _ = setup_attempt(api)
    rid = r["runner_id"]
    now = (int(time.time()) // 60 - 1) * 60
    def mac(at):
        return point(at, cpu=10, mem=.85) | {"cpu": 10, "swap_used": None, "swap_total": None,
                                            "load_swap_used": 95, "load_swap_total": 100}
    post(api, "runners/performance", {"samples": [mac(now - 60*i) for i in range(9, -1, -1)]}, token=r["token"])
    with api.app.state.store.read() as c:
        assert P.load_status(c, rid)["latest_sample"]["swap_used"] == 95
        assert c.execute("SELECT count(*) FROM perf_overloads WHERE runner_id=?", (rid,)).fetchone()[0] == 0
        assert not any(P.reasons(p) for p in c.execute("SELECT * FROM perf_points WHERE runner_id=?", (rid,)))
    # The existing fixed-swap Linux diagnostic rule still trips on the same raw numbers.
    with api.app.state.store.transaction() as c:
        P.ingest(c, rid, [M.PerformanceSample(**(mac(now + 60*i) | {"swap_used": 95, "swap_total": 100}))
                          for i in range(1, 11)], now=now+600)
        assert json.loads(c.execute("SELECT reasons FROM perf_overloads WHERE runner_id=?", (rid,)).fetchone()[0]) == ["swap"]


def test_older_runner_snapshot_fallback_is_timestamped_and_off_or_stale_is_unknown(api):
    r, _, a = setup_attempt(api)
    rid, aid = r["runner_id"], a["id"]
    with api.app.state.store.transaction() as c:
        P.stamp_attempt(c, aid, rid, "start")
        assert c.execute("SELECT count(*) FROM attempt_load_snapshots WHERE attempt_id=?", (aid,)).fetchone()[0] == 0
    at = int(time.time()) - 30
    post(api, "runners/performance", {"samples": [point(at, cpu=45)]}, token=r["token"])
    post(api, f"attempts/{aid}/started", {"thread_id": "older-runner"}, token=r["token"])
    with api.app.state.store.transaction() as c:
        assert P.attempt_snapshots(c, aid)["load_start"] == {"at": at, "cpu_pct": 45, "mem_pct": 50, "running_turns": 3}
        c.execute("UPDATE runner_load_samples SET sampled_at=? WHERE runner_id=?", (at-300, rid))
    post(api, f"attempts/{aid}/complete", {"outcome": "completed", "text": "Done", "last_seq": 0}, token=r["token"])
    with api.app.state.store.transaction() as c:
        assert P.attempt_snapshots(c, aid)["load_end"] is None
        c.execute("UPDATE runners SET performance='off' WHERE id=?", (rid,))
        P.stamp_attempt(c, aid, rid, "end", M.LoadSnapshot(at=at, cpu_pct=10, mem_pct=10, running_turns=1))
        assert P.attempt_snapshots(c, aid)["load_end"] is None
