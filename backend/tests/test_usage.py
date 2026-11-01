"""Usage (backend/usage.py, docs/usage.md): a run's tokens are stored with its model and priced from the
catalog's price table, the API groups them by bot, day or routine over a date range, and it shows a person
only the bots they run or own. Subscription runs are counted apart from API spend."""

import json
import sqlite3
from contextlib import closing

import pytest

from backend import hubdb, providers
from backend.tests.test_api import api, as_member, headers, post, runner, setup_attempt  # noqa: F401

DAY = "2026-09-20"


def price(model, uncached, cached, output):
    inp, hit, out = providers.PRICES[model]
    return round((uncached * inp + cached * hit + output * out) / 1e6, 6)


def complete(api, r, attempt, usage=None, **more):
    post(api, f"attempts/{attempt['id']}/started", {"thread_id": "thread-1"}, token=r["token"])
    body = {"outcome": "completed", "text": "Done.", "last_seq": 0, "tokens_in": 3000, "tokens_out": 500, **more}
    if usage is not None:
        body["usage"] = usage
    return post(api, f"attempts/{attempt['id']}/complete", body, token=r["token"])


def turn(api, aid):
    with api.app.state.store.read() as c:
        return dict(c.execute("SELECT * FROM turns WHERE id=?", (aid,)).fetchone())


def test_a_finished_run_keeps_its_tokens_model_and_list_price_cost(api):
    r, _, attempt = setup_attempt(api)
    complete(api, r, attempt, {"input_tokens": 1000, "cached_tokens": 2000, "output_tokens": 500,
                               "model": "gpt-6-sol", "runtime": "codex", "billing": "api"})
    row = turn(api, attempt["id"])
    assert (row["input_tokens"], row["cached_tokens"], row["output_tokens"]) == (1000, 2000, 500)
    assert (row["model"], row["provider"], row["billing"]) == ("gpt-6-sol", "openai", "api")
    assert row["est_cost_usd"] == pytest.approx(price("gpt-6-sol", 1000, 2000, 500))    # 0.0074


def test_anthropic_cache_hits_follow_the_pricing_pages_multipliers():
    """Anthropic's page prices a cache hit at 0.025x input on Fable 5.1, 0.05x on Opus 5.5 and Sonnet 5.5."""
    for model, ratio in (("claude-fable-5-1", 0.025), ("claude-opus-5-5", 0.05), ("claude-sonnet-5-5", 0.05),
                         ("claude-sonnet-5", 0.1), ("claude-haiku-4-5", 0.1)):
        inp, hit, _ = providers.PRICES[model]
        assert hit == pytest.approx(inp * ratio), model


def test_the_migration_can_run_again_and_a_file_that_has_the_columns_catches_up(tmp_path):
    conn = hubdb.connect(tmp_path / "hub.db")
    assert conn.execute("PRAGMA user_version").fetchone()[0] == len(hubdb.MIGRATIONS)
    columns = {row[1] for row in conn.execute("PRAGMA table_info(turns)")}
    assert {"input_tokens", "cached_tokens", "output_tokens", "model", "provider", "est_cost_usd", "billing"} <= columns
    conn.execute("PRAGMA user_version=%d" % (len(hubdb.MIGRATIONS) - 1))
    hubdb.migrate(conn)                                                # the change is there but the version is not
    hubdb.migrate(conn)
    assert conn.execute("PRAGMA user_version").fetchone()[0] == len(hubdb.MIGRATIONS)


# --------------------------------------------------------------------------- the API
def seed(api):
    """Runs on 20 and 21 September: ops (Ana's, no department) and cpo (Ben's, product), one routine."""
    with api.app.state.store.transaction() as c:
        people = json.loads(c.execute("SELECT value FROM registry_metadata WHERE key='people'").fetchone()[0])
        c.execute("UPDATE registry_metadata SET value=? WHERE key='people'",
                  (json.dumps({**people, "teams": {"product": {"root": "cpo"}}}),))
    t = post_task(api, "ops")
    with api.app.state.store.transaction() as c:
        c.execute("INSERT INTO schedules(id, bot, cron, title) VALUES('s1','ops','0 9 * * *','Morning digest')")
        c.execute("INSERT INTO schedule_occurrences(schedule_id, occurrence, task_id, outcome) VALUES('s1','o1',?,'created')", (t,))
        rows = [
            # id, bot, day, tokens (in, cached, out), model, est, billing, task
            ("t1", "ops", DAY, (1000, 4000, 500), "gpt-6-sol", 0.0078, "api", t),
            ("t2", "ops", DAY, (2000, 0, 100), "gpt-6-sol", 0.005, "api", None),
            ("t3", "ops", "2026-09-21", (500, 0, 50), "claude-opus-5", 0.0038, "subscription", None),
            ("t4", "cpo", "2026-09-21", (100, 0, 10), "mystery-model", None, "api", None),
            ("t5", "cpo", "2026-09-21", (1_000_000, 0, 100_000), "claude-opus-5", 7.5, "api", None),
            ("t6", "ops", "2026-08-01", (10, 0, 1), "gpt-6-sol", 0.01, "api", None),       # outside the range
        ]
        for tid, bot, day, (i, cache, o), model, est, billing, task in rows:
            c.execute("INSERT INTO turns(id, bot, started_at, finished_at, exit, task_id, input_tokens, cached_tokens, output_tokens, "
                      "model, est_cost_usd, billing) VALUES(?,?,?,?, 'completed', ?,?,?,?,?,?,?)",
                      (tid, bot, day + "T10:00:00.000000Z", day + "T10:05:00.000000Z", task, i, cache, o, model, est, billing))
        c.execute("INSERT INTO turns(id, bot, started_at) VALUES('running','ops',?)", (DAY + "T11:00:00.000000Z",))   # not finished


def post_task(api, owner):
    return post(api, "tasks", {"owner": owner, "title": "Digest", "body": "Do it."})["id"]


def usage(api, token="ana-test", expected=200, **params):
    r = api.get("/api/v2/usage", params={"from": DAY, "to": "2026-09-21", **params}, headers=headers(token))
    assert r.status_code == expected, r.text
    return r.json()


def test_usage_by_bot_is_sorted_by_spend_with_totals_and_subscription_kept_apart(api):
    seed(api)
    got = usage(api)
    assert got["prices_as_of"] == providers.PRICES_AS_OF and got["group"] == "bot"
    assert [r["bot"] for r in got["rows"]] == ["cpo", "ops"]
    cpo, ops = got["rows"]
    assert cpo["runs"] == 2 and cpo["est_cost_usd"] == 7.5 and cpo["subscription_equiv_usd"] == 0
    assert cpo["department"] == "product" and ops["department"] is None
    assert (ops["runs"], ops["input_tokens"], ops["cached_tokens"], ops["output_tokens"]) == (3, 3500, 4000, 650)
    assert ops["est_cost_usd"] == pytest.approx(0.0128) and ops["subscription_equiv_usd"] == pytest.approx(0.0038)
    assert got["totals"]["runs"] == 5 and got["totals"]["est_cost_usd"] == pytest.approx(7.5128)
    assert got["totals"]["subscription_equiv_usd"] == pytest.approx(0.0038)
    assert got["totals"]["unpriced_runs"] == 1                                  # the model with no price
    assert sum(r["share"] for r in got["rows"]) == pytest.approx(1, abs=1e-3) and cpo["share"] > 0.99


def test_usage_does_not_treat_unreported_zero_as_a_priced_run(api):
    with api.app.state.store.transaction() as c:
        c.executemany("INSERT INTO turns(id,bot,started_at,finished_at,input_tokens,cached_tokens,output_tokens,"
                      "est_cost_usd,billing,usage_reason) VALUES(?,'ops',?,?,0,0,0,0,?,?)", [
            (reason, DAY + "T10:00:00Z", DAY + "T10:05:00Z", billing, reason)
            for reason, billing in (("usage_unreported", "api"), ("usage_limited", None), ("no_result", "api"))])
    def amounts():
        grouped = usage(api)
        detail = usage(api, bot="ops")
        return [grouped["totals"], *grouped["rows"], detail["totals"], *detail["routines"],
                *[r for r in detail["daily"] if r["runs"]]]
    assert all(r["est_cost_usd"] is None and r["unpriced_runs"] == 3 for r in amounts())
    with api.app.state.store.transaction() as c:
        c.execute("INSERT INTO turns(id,bot,started_at,finished_at,input_tokens,est_cost_usd,billing,usage_reason) "
                  "VALUES('unknown','ops',?,?,100,NULL,'api','cost_unpriced')",
                  (DAY + "T10:00:00Z", DAY + "T10:05:00Z"))
    assert all(r["est_cost_usd"] is None and r["input_tokens"] == 100 for r in amounts())
    with api.app.state.store.transaction() as c:
        c.execute("INSERT INTO turns(id,bot,started_at,finished_at,est_cost_usd,billing) "
                  "VALUES('measured','ops',?,?,2.5,'api')", (DAY + "T10:00:00Z", DAY + "T10:05:00Z"))
    assert all(r["est_cost_usd"] == 2.5 for r in amounts())
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE turns SET est_cost_usd=0 WHERE id='measured'")
    assert all(r["est_cost_usd"] == 0 for r in amounts())


def test_the_owner_and_admins_see_every_bot_and_a_member_only_the_ones_they_run(api):
    seed(api)
    assert {r["bot"] for r in usage(api, "ben-test")["rows"]} == {"ops", "cpo"}        # Ben is an administrator
    assert usage(api, "cara-test")["rows"] == [] and usage(api, "cara-test")["totals"]["runs"] == 0
    as_member(api, "ben@acme.example")
    assert [r["bot"] for r in usage(api, "ben-test")["rows"]] == ["cpo"]                # his own, not Ana's
    assert usage(api, "ben-test", bot="cpo")["totals"]["runs"] == 2
    usage(api, "ben-test", bot="ops", expected=403)
    usage(api, "cara-test", bot="cpo", expected=403)
    # The one that runs a bot may also hold Read on it; not running it is what keeps a member out.
    assert usage(api, "ana-test")["totals"]["runs"] == 5


def test_a_bot_or_a_computer_cannot_ask(api):
    r = runner(api)
    assert api.get("/api/v2/usage", headers=headers(r["token"])).status_code in (401, 403)


# --------------------------------------------------------------------------- long prompts (providers.LONG_PROMPT)
def haiku(requests=None, **tokens):
    part = {"model": "claude-haiku-5-5", "runtime": "claude", "billing": "api", **tokens}
    if requests is not None:
        part["requests"] = requests
    return {**part, "segments": [part]}


def today(api):
    day = hubdb.now()[:10]
    return usage(api, **{"from": day, "to": day})["totals"]


def test_each_haiku_request_pays_the_rate_for_its_own_prompt_length(api):
    r, _, attempt = setup_attempt(api)
    short, long = [1_000, 50_000, 800], [5_000, 115_000, 2_000]          # prompts of 51,000 and 120,000 tokens
    complete(api, r, attempt, haiku([short, long], input_tokens=6_000, cached_tokens=165_000, output_tokens=2_800))
    under = (1_000 * 0.10 + 50_000 * 0.01 + 800 * 0.50) / 1e6           # Anthropic's page, read 2026-10-17
    over = (5_000 * 0.50 + 115_000 * 0.05 + 2_000 * 2.50) / 1e6
    assert turn(api, attempt["id"])["est_cost_usd"] == pytest.approx(under + over)
    assert today(api)["low_estimate_runs"] == 0
    # A prompt of exactly 100,000 tokens is not over it.
    assert providers.estimate("claude-haiku-5-5", 1_000, 99_000, 10, [[1_000, 99_000, 10]]) == (
        pytest.approx(price("claude-haiku-5-5", 1_000, 99_000, 10)), False)


def test_without_each_request_the_estimate_may_be_low_and_other_models_are_unchanged(api):
    r, _, attempt = setup_attempt(api)
    complete(api, r, attempt, haiku(input_tokens=6_000, cached_tokens=165_000, output_tokens=2_800))   # an older runner
    assert turn(api, attempt["id"])["est_cost_usd"] == pytest.approx(price("claude-haiku-5-5", 6_000, 165_000, 2_800))
    assert today(api)["low_estimate_runs"] == 1
    # A model with no long-prompt rate is priced from its totals, whatever its requests' prompts.
    assert providers.estimate("claude-sonnet-5-5", 5_000, 115_000, 2_000, [[5_000, 115_000, 2_000]]) == (
        pytest.approx(price("claude-sonnet-5-5", 5_000, 115_000, 2_000)), False)


def test_tico_cheap_1_1_bills_a_prompt_over_100k_whole_at_its_long_rate():
    # 100,000 prompt tokens is the standard Cheap price; one more and the whole request is at $0.75 / $3.75, cached 10%.
    assert providers.estimate("tico-cheap-1.1", 100_000, 0, 1_000, [[100_000, 0, 1_000]]) == (
        pytest.approx(0.01575), False)
    assert providers.estimate("tico-cheap-1.1", 100_001, 0, 1_000, [[100_001, 0, 1_000]]) == (
        pytest.approx(0.07875075), False)
    assert providers.estimate("tico-cheap-1.1", 1, 100_000, 0, [[1, 100_000, 0]])[0] == pytest.approx(
        (1 * 0.75 + 100_000 * 0.075) / 1e6)
    # Cheap 1.0 keeps its one price.
    assert providers.estimate("tico-cheap-1.0", 100_001, 0, 1_000, [[100_001, 0, 1_000]])[0] == pytest.approx(
        (100_001 * 0.15 + 1_000 * 0.75) / 1e6)


@pytest.mark.parametrize("model", ["tico-cheap-1.1", "tico-mid-1.0"])
def test_a_tico_run_prices_each_request_on_its_own_tier_and_other_tico_models_keep_their_totals(api, model):
    r, _, attempt = setup_attempt(api)
    short, long = [1_000, 50_000, 800], [5_000, 115_000, 2_000]          # prompts of 51,000 and 120,000 tokens
    part = {"model": model, "runtime": "pi", "billing": "api", "input_tokens": 6_000, "cached_tokens": 165_000,
            "output_tokens": 2_800, "requests": [short, long]}
    complete(api, r, attempt, {**part, "segments": [part]})
    if model == "tico-cheap-1.1":
        expected = (1_000 * 0.15 + 50_000 * 0.015 + 800 * 0.75 + 5_000 * 0.75 + 115_000 * 0.075 + 2_000 * 3.75) / 1e6
    else:
        expected = price(model, 6_000, 165_000, 2_800)
    assert turn(api, attempt["id"])["est_cost_usd"] == pytest.approx(expected)
    assert today(api)["low_estimate_runs"] == 0


def zero_usage(row, reason):
    assert (row["input_tokens"], row["cached_tokens"], row["output_tokens"], row["est_cost_usd"],
            row["usage_reason"]) == (0, 0, 0, 0, reason)
    assert all(row[key] is None for key in ("tokens_in", "tokens_out", "cost"))


@pytest.mark.parametrize("outcome,limited,reason", [
    ("failed", False, "usage_unreported"),
    ("interrupted", False, "usage_unreported"),
    ("failed", True, "usage_limited"),
])
def test_results_with_no_counts_end_with_zero_usage_and_their_reason(api, outcome, limited, reason):
    r, _, attempt = setup_attempt(api)
    complete(api, r, attempt, outcome=outcome, limited=limited, tokens_in=None, tokens_out=None)
    row = turn(api, attempt["id"])
    assert row["finished_at"] and row["exit"] == outcome
    zero_usage(row, reason)


def test_a_run_whose_lease_runs_out_ends_with_zero_usage_and_no_result(api):
    from backend.tests.test_api import expire
    r, _, attempt = setup_attempt(api)
    post(api, f"attempts/{attempt['id']}/started", {"thread_id": "thread-1"}, token=r["token"])
    expire(api, attempt["id"])
    with api.app.state.store.transaction() as c:
        api.app.state.execution.expire(c)
    row = turn(api, attempt["id"])
    assert row["finished_at"] and row["exit"] in ("interrupted", "failed")
    zero_usage(row, "no_result")


def test_a_late_limited_result_keeps_usage_without_reopening_the_dismissed_job(api):
    from backend.tests.test_execution_review import aged, finished_then_lapsed, settled
    r, _, attempt = finished_then_lapsed(api)
    aged(api, attempt["id"])
    post(api, f"jobs/{attempt['job_id']}/reconcile", {
        "attempt_id": attempt["id"], "decision": "dismiss", "note": "Nothing left to send.",
        "acknowledge_uncertain_effects": True})
    result = post(api, f"attempts/{attempt['id']}/complete", {
        "outcome": "failed", "text": "Usage limit reached.", "last_seq": 3, "limited": True}, token=r["token"])
    assert result["filed"] is True and settled(api, attempt) == ("expired", "cancelled")
    zero_usage(turn(api, attempt["id"]), "usage_limited")


def test_a_late_completed_result_keeps_measured_usage_without_reopening_the_dismissed_job(api):
    from backend.tests.test_execution_review import aged, finished_then_lapsed, settled
    r, _, attempt = finished_then_lapsed(api)
    aged(api, attempt["id"])
    post(api, "bots/ops/stop", {"attempt_id": attempt["id"]})
    post(api, f"jobs/{attempt['job_id']}/reconcile", {
        "attempt_id": attempt["id"], "decision": "dismiss", "note": "Nothing left to send.",
        "acknowledge_uncertain_effects": True})
    result = post(api, f"attempts/{attempt['id']}/complete", {
        "outcome": "completed", "text": "Done.", "last_seq": 3,
        "usage": {"input_tokens": 10, "cached_tokens": 20, "output_tokens": 5,
                  "model": "gpt-6-sol", "runtime": "codex", "billing": "api"}}, token=r["token"])
    assert result["filed"] is True and settled(api, attempt) == ("expired", "cancelled")
    row = turn(api, attempt["id"])
    assert (row["input_tokens"], row["cached_tokens"], row["output_tokens"]) == (10, 20, 5)
    assert row["est_cost_usd"] == pytest.approx(price("gpt-6-sol", 10, 20, 5))
    assert row["usage_reason"] is None


def test_a_restore_zeroes_open_runs_and_keeps_a_finished_run_as_measured(api, tmp_path):
    from backend.backup import restore_snapshot, snapshot
    r, _, done = setup_attempt(api)
    complete(api, r, done, {"input_tokens": 10, "cached_tokens": 20, "output_tokens": 5,
                            "model": "gpt-6-sol", "runtime": "codex", "billing": "api"})
    before = turn(api, done["id"])
    post(api, "chat/ops", {"text": "One more."})
    from backend.tests.test_api import claim
    open_run = claim(api, r)
    post(api, f"attempts/{open_run['id']}/started", {"thread_id": "pre-disaster"}, token=r["token"])
    backup, restored = tmp_path / "backup.sqlite", tmp_path / "restored.sqlite"
    snapshot(api.app.state.store.settings.db_path, backup)
    restore_snapshot(backup, restored)
    with closing(sqlite3.connect(restored)) as db:
        db.row_factory = sqlite3.Row
        rows = {x["id"]: dict(x) for x in db.execute("SELECT * FROM turns")}
    zero_usage(rows[open_run["id"]], "no_result")
    assert rows[open_run["id"]]["exit"] == "interrupted"
    kept = rows[done["id"]]
    assert {k: kept[k] for k in ("input_tokens", "cached_tokens", "output_tokens", "est_cost_usd", "usage_reason")} \
        == {k: before[k] for k in ("input_tokens", "cached_tokens", "output_tokens", "est_cost_usd", "usage_reason")}
    assert kept["usage_reason"] is None


def test_a_measured_run_has_no_usage_reason(api):
    r, _, attempt = setup_attempt(api)
    complete(api, r, attempt, {"input_tokens": 10, "cached_tokens": 0, "output_tokens": 5,
                               "model": "gpt-6-sol", "runtime": "codex", "billing": "api"})
    assert turn(api, attempt["id"])["usage_reason"] is None
