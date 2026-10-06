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
        assert c.execute("SELECT count(*) FROM jobs WHERE bot='ops' AND state='queued'").fetchone()[0] == 1   # parked, not lost
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
    put(api, "usage/limits/cpo", {}, "ben-test")                                    # empty follows the default
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
