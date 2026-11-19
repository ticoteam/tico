"""A Claude plan's week read on request: fixture computers and synthetic answers. No provider request, no `claude` run."""

from datetime import datetime, timedelta, timezone

from backend import subscription_refresh as SR
from backend.tests.helpers.api import assign, get, headers, post, runner
from backend.tests.helpers.model_subscriptions import TOKEN, add, give


def computer(api):
    """A computer that names its own login (as `beat({})` does) and can read a plan's week."""
    m = runner(api, label="Plan Mac")
    assign(api, m, "ops")
    post(
        api,
        "runners/heartbeat",
        {
            "version": "test",
            "platform": "test",
            "capabilities": ["model_subscriptions_v1", "computer_logins_v1", SR.PLAN_CAPABILITY],
            "readiness": {
                "schema_version": 1,
                "bots": {"ops": {"runtime": "claude", "ready": True}},
                "computer_profile": "own",
                "runtimes": {"claude": {"installed": True, "authenticated": "ready"}},
            },
            "profiles": [],
        },
        token=m["token"],
    )
    return m


def row(api, sid):
    return next(s for s in get(api, "model-subscriptions")["subscriptions"] if s["id"] == sid)


def reset_in(days):
    return (datetime.now(timezone.utc) + timedelta(days=days)).isoformat()


def test_an_admin_reads_a_claude_plans_week_on_its_computer_and_it_becomes_the_plans_reading(api):
    sid = add(api)["id"]
    m = computer(api)
    give(api, m, "claude", sid)
    asked = post(api, f"model-subscriptions/{sid}/usage-refresh", {})
    assert asked["state"] == "requested"
    # The computer is told at once (a `runner` change), not left to its five-minute backup poll, after the read lapsed.
    with api.app.state.store.read() as c:
        told = c.execute(
            "SELECT subject_id FROM changes WHERE topic='runner' AND kind='subscription_refresh'"
        ).fetchall()
    assert m["runner_id"] in {r[0] for r in told}
    [read] = get(api, "runner-subscription-refreshes", m["token"])["refreshes"]
    assert read["subscription"] == sid and read["env"] == {"CLAUDE_CODE_OAUTH_TOKEN": TOKEN}
    post(
        api,
        f"runner-subscription-refreshes/{read['id']}/report",
        {
            "profile": "",
            "runtime": "claude",
            "state": "succeeded",
            "subscription": sid,
            "plan": {"used_percent": 42, "resets_at": reset_in(3)},
        },
        token=m["token"],
    )
    listed = row(api, sid)
    assert listed["usage"]["window"] == "week" and listed["usage"]["used_pct"] == 42
    assert listed["usage_refresh"]["state"] == "succeeded"
    assert get(api, "runner-subscription-refreshes", m["token"])["refreshes"] == []


def test_only_an_admin_asks_one_read_at_a_time_and_another_request_is_refused(api):
    sid = add(api)["id"]
    m = computer(api)
    give(api, m, "claude", sid)
    refused = api.post(f"/api/v2/model-subscriptions/{sid}/usage-refresh", json={}, headers=headers("cara-test"))
    assert refused.status_code == 403
    first = post(api, f"model-subscriptions/{sid}/usage-refresh", {})
    # Within the cooldown, the same read answers again: no second request is queued.
    assert post(api, f"model-subscriptions/{sid}/usage-refresh", {})["requested_at"] == first["requested_at"]
    # A computer answers only the read it was asked, by its id.
    post(
        api,
        "runner-subscription-refreshes/not-the-request/report",
        {"profile": "", "runtime": "claude", "state": "failed", "subscription": sid},
        token=m["token"],
        expected=404,
    )


def test_a_read_whose_reset_is_not_within_a_week_is_refused(api):
    sid = add(api)["id"]
    m = computer(api)
    give(api, m, "claude", sid)
    post(api, f"model-subscriptions/{sid}/usage-refresh", {})
    [read] = get(api, "runner-subscription-refreshes", m["token"])["refreshes"]
    for at in (reset_in(-1), reset_in(9)):
        post(
            api,
            f"runner-subscription-refreshes/{read['id']}/report",
            {
                "profile": "",
                "runtime": "claude",
                "state": "succeeded",
                "subscription": sid,
                "plan": {"used_percent": 42, "resets_at": at},
            },
            token=m["token"],
            expected=422,
        )
