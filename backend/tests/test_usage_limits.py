"""Spend limits (backend/usage_limits.py, docs/usage.md): a bot over a limit takes no new job and a run already going
finishes, the 80% notice comes once, raising the limit resumes the bot, and who may set one."""

from backend.tests.test_api import api, as_member, assign, claim, get, headers, post, ready, runner  # noqa: F401
from backend.tests.test_usage import complete


def put(api, path, body, token="ana-test", expected=200):
    r = api.put("/api/v2/" + path, json=body, headers=headers(token))
    assert r.status_code == expected, r.text
    return r.json()


def cost(output_tokens):
    """A usage report priced by gpt-6-sol at $10 per million output tokens."""
    return {"input_tokens": 0, "cached_tokens": 0, "output_tokens": output_tokens, "model": "gpt-6-sol", "runtime": "codex"}


def start(api, r, text="Please look at this."):
    post(api, "chat/ops", {"text": text})
    attempt = claim(api, r)
    return attempt


def finish(api, r, attempt, output_tokens):
    complete(api, r, attempt, cost(output_tokens))


def fleet(api):
    r = runner(api)
    assign(api, r, "ops")
    ready(api, r, ["ops"])
    return r


def paused(api):
    status = get(api, "status")["bots"]
    return next((s for s in status if s["bot"] == "ops"), {})


def test_a_bot_over_its_limit_takes_no_new_job_a_running_turn_finishes_and_raising_it_resumes(api):
    r = fleet(api)
    put(api, "usage/limits/ops", {"daily_usd": 1})
    first = start(api, r)
    post(api, "chat/ops", {"text": "And this one after."})                       # waits while the first runs
    put(api, "usage/limits/ops", {"daily_usd": 0.5})                              # tightened mid-run: the run is not touched
    finish(api, r, first, 500_000)                                                # $5 against a $0.50 limit
    assert claim(api, r) is None                                                  # over the limit: nothing new starts
    assert paused(api)["state"] == "paused" and paused(api)["focus"] == "Paused: over its daily limit"
    with api.app.state.store.read() as c:
        assert c.execute("SELECT count(*) FROM jobs WHERE bot_id='ops' AND state='queued'").fetchone()[0] == 1   # parked, not lost
    put(api, "usage/limits/ops", {"daily_usd": 50})
    assert claim(api, r) is not None                                              # raising the limit resumes it
    assert paused(api)["state"] != "paused"


def test_the_monthly_limit_and_the_company_default_block_too(api):
    r = fleet(api)
    put(api, "usage/limits", {"monthly_usd": 4})                                  # ops has no limit of its own
    first = start(api, r)
    finish(api, r, first, 500_000)
    post(api, "chat/ops", {"text": "More."})
    assert claim(api, r) is None
    shown = get(api, "usage/limits")["bots"]["ops"]
    assert shown["blocked"] == "monthly" and shown["source"]["monthly"] == "company" and shown["month_spent"] == 5
    put(api, "usage/limits/ops", {"monthly_usd": 100})                            # its own limit wins over the default
    assert claim(api, r) is not None


def test_who_may_set_a_limit(api):
    put(api, "usage/limits", {"daily_usd": 3})
    # Ben is an administrator: any bot, and the default.
    put(api, "usage/limits/ops", {"daily_usd": 30}, "ben-test")
    put(api, "usage/limits", {"daily_usd": 3}, "ben-test")
    as_member(api, "ben@acme.example")
    mine = get(api, "usage/limits", "ben-test")
    assert sorted(mine["bots"]) == ["cpo", "product-design"] and mine["may_edit_default"] is False
    assert mine["default"]["daily_usd"] == 3
    put(api, "usage/limits", {"daily_usd": 300}, "ben-test", expected=403)          # not the company's
    put(api, "usage/limits/ops", {"daily_usd": 1}, "ben-test", expected=403)        # not his bot
    put(api, "usage/limits/cpo", {"daily_usd": 5}, "ben-test", expected=403)        # above the company default
    put(api, "usage/limits/cpo", {"daily_usd": 2, "monthly_usd": 40}, "ben-test")   # below it; no default for monthly
    put(api, "usage/limits/cpo", {}, "ben-test")                                    # empty changes nothing
    assert get(api, "usage/limits", "ben-test")["bots"]["cpo"]["daily_usd"] == 2
    put(api, "usage/limits/cpo", {"daily_usd": None}, "ben-test")                   # cleared, it follows the default
    assert get(api, "usage/limits", "ben-test")["bots"]["cpo"]["daily_usd"] == 3
    put(api, "usage/limits/cpo", {"daily_usd": 2}, "cara-test", expected=403)       # she sees it, does not run it
    put(api, "usage/limits/inbox", {"daily_usd": 2}, "cara-test", expected=404)     # Ana's private bot is not even there
    assert get(api, "usage/limits", "cara-test")["bots"] == {}
    for bad in ({"daily_usd": 0}, {"daily_usd": -1}, {"monthly_usd": 1e12}):
        put(api, "usage/limits/ops", bad, "ana-test", expected=422)


def test_a_computer_cannot_set_or_read_limits(api):
    r = runner(api)
    assert api.get("/api/v2/usage/limits", headers=headers(r["token"])).status_code in (401, 403)
    assert api.put("/api/v2/usage/limits/ops", json={"daily_usd": 1}, headers=headers(r["token"])).status_code in (401, 403)


def test_a_message_or_task_for_a_bot_over_its_limit_is_saved_and_the_sender_is_told_why_it_waits(api):
    from backend.tests.test_mcp import call
    r = fleet(api)
    put(api, "usage/limits/ops", {"daily_usd": 1})
    finish(api, r, start(api, r), 500_000)                                        # $5 against a $1 limit
    err, sent = call(api, "hub_message_send", {"to": "ops", "text": "Check the Acme invoice."})
    assert not err and sent["id"]                                                 # saved: it runs once the limit allows
    assert sent["warning"] == ("ops is over its daily limit ($5.00 of $1.00); it starts again at 00:00 UTC "
                               "or when a person raises the limit.")
    err, made = call(api, "hub_task_create", {"owner_id": "ops", "title": "Review the Acme invoice", "body": "Please."})
    assert not err and made["task"]["id"] and "over its daily limit" in made["warning"]
    assert paused(api)["limit"] == {**paused(api)["limit"], "period": "daily", "spent": 5, "limit": 1}
    put(api, "usage/limits/ops", {"daily_usd": 50})
    err, sent = call(api, "hub_message_send", {"to": "ops", "text": "And this."})
    assert not err and "warning" not in sent


def groups(api, ops_team):
    """Engineering with Product nested under it; ops in `ops_team`."""
    import json
    with api.app.state.store.transaction() as c:
        people = json.loads(c.execute("SELECT value FROM registry_metadata WHERE key='people'").fetchone()[0])
        people["org_groups"] = {"eng": {"name": "Engineering"}, "product": {"name": "Product", "parent": "eng"}}
        c.execute("UPDATE registry_metadata SET value=? WHERE key='people'", (json.dumps(people),))
        c.execute("UPDATE bot_config SET team=? WHERE bot='ops'", (ops_team,))


def test_a_group_or_team_limit_holds_every_bot_it_covers_and_an_hourly_limit_set_before_counts_the_hour(api):
    from datetime import datetime
    from backend import usage_limits
    from backend.tests.test_mcp import call
    r = fleet(api)
    groups(api, "product")                                                        # ops is in Product, under Engineering
    put(api, "usage/pools/groups/eng", {"daily_usd": 1})
    finish(api, r, start(api, r), 500_000)                                        # $5 against the group's $1
    post(api, "chat/ops", {"text": "More."})
    assert claim(api, r) is None                                                  # the parent group's limit holds it
    assert paused(api)["focus"] == "Paused: the Engineering group is over its daily limit"
    assert "spent" not in paused(api)["limit"]                                    # the group's total is not every reader's
    err, sent = call(api, "hub_message_send", {"to": "ops", "text": "Check the Acme invoice."})
    assert not err and "the Engineering group is over its daily limit" in sent["warning"]
    pools = get(api, "usage/limits")["pools"]
    assert pools["groups"]["eng"]["day_spent"] == 5 and pools["groups"]["eng"]["blocked"] == "daily"
    put(api, "usage/pools/groups/eng", {"daily_usd": None})                       # nothing left removes it
    with api.app.state.store.read() as c:
        assert c.execute("SELECT count(*) FROM usage_pools WHERE scope='group:eng'").fetchone()[0] == 0
    put(api, "usage/pools/company", {"weekly_usd": 4})
    assert claim(api, r) is None                                                  # the whole team's week is spent too
    assert paused(api)["focus"] == "Paused: the team is over its weekly limit"
    put(api, "usage/pools/company", {"weekly_usd": 400})
    assert claim(api, r) is not None
    with api.app.state.store.transaction() as c:                                  # an hour limit set before the half hour
        c.execute("INSERT INTO usage_limits(bot, hourly_usd) VALUES('ops', 1)")
    with api.app.state.store.read() as c:
        started = c.execute("SELECT min(started_at) FROM turns WHERE bot='ops'").fetchone()[0]
        at = datetime.fromisoformat(started[:19]).replace(tzinfo=usage_limits.timezone.utc)
        assert usage_limits.blocked(c, "ops", at=at) == "hourly"                  # the run's own hour
        assert usage_limits.blocked(c, "ops", at=at + usage_limits.timedelta(hours=1)) is None


def test_only_the_owner_and_administrators_set_team_and_group_limits(api):
    groups(api, "eng")
    put(api, "usage/pools/company", {"per_30m_usd": 10}, "ben-test")              # Ben is an administrator
    put(api, "usage/pools/groups/nope", {"per_30m_usd": 10}, expected=404)
    as_member(api, "ben@acme.example")
    put(api, "usage/pools/company", {"per_30m_usd": 100}, "ben-test", expected=403)
    put(api, "usage/pools/groups/eng", {"per_30m_usd": 1}, "ben-test", expected=403)
    assert get(api, "usage/limits", "ben-test")["pools"] is None
    assert get(api, "usage/limits")["pools"]["company"]["per_30m_usd"] == 10


def test_subscription_runs_count_unless_the_team_turns_that_off(api):
    assert get(api, "usage/limits")["default"]["count_subscription"] is True       # never chosen
    assert put(api, "usage/limits", {"daily_usd": 20, "count_subscription": False})["default"]["count_subscription"] is False
    assert get(api, "usage/limits")["default"]["count_subscription"] is False      # a saved choice stays
    assert put(api, "usage/limits", {"daily_usd": 20})["default"]["count_subscription"] is False   # left out keeps it
    assert put(api, "usage/limits", {"count_subscription": True})["default"]["count_subscription"] is True
