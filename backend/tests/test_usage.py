"""Usage (backend/usage.py, docs/usage.md): a run's tokens are stored with its model and priced from the
catalog's price table, the API groups them by bot, day or routine over a date range, and it shows a person
only the bots they run or own. Subscription runs are counted apart from API spend."""

import json

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
        people = json.loads(c.execute("SELECT value_json FROM registry_metadata WHERE key='people'").fetchone()[0])
        c.execute("UPDATE registry_metadata SET value_json=? WHERE key='people'",
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
            c.execute("INSERT INTO turns(id, bot, started, finished, exit, task_id, input_tokens, cached_tokens, output_tokens, "
                      "model, est_cost_usd, billing) VALUES(?,?,?,?, 'completed', ?,?,?,?,?,?,?)",
                      (tid, bot, day + "T10:00:00.000000Z", day + "T10:05:00.000000Z", task, i, cache, o, model, est, billing))
        c.execute("INSERT INTO turns(id, bot, started) VALUES('running','ops',?)", (DAY + "T11:00:00.000000Z",))   # not finished


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
