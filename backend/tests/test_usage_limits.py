"""Spend limits (backend/usage_limits.py, docs/usage.md): a bot over a limit takes no new job and a run already going
finishes, the 80% notice comes once, raising the limit resumes the bot, and who may set one."""

import pytest

from backend.tests.helpers.api import as_member, assign, claim, get, headers, post, ready, runner  # noqa: F401
from backend.tests.helpers.usage_limits import finish, fleet, groups, paused, put, start

pytestmark = pytest.mark.usefixtures("inside_one_half_hour")


def test_a_bot_over_its_limit_takes_no_new_job_a_running_turn_finishes_and_raising_it_resumes(api):
    r = fleet(api)
    put(api, "usage/limits/ops", {"daily_usd": 1})
    first = start(api, r)
    post(api, "chat/ops", {"text": "And this one after."})  # waits while the first runs
    put(api, "usage/limits/ops", {"daily_usd": 0.5})  # tightened mid-run: the run is not touched
    finish(api, r, first, 500_000)  # $5 against a $0.50 limit
    assert claim(api, r) is None  # over the limit: nothing new starts
    assert paused(api)["state"] == "paused" and paused(api)["focus"] == "Paused: over its daily limit"
    with api.app.state.store.read() as c:
        assert (
            c.execute("SELECT count(*) FROM jobs WHERE bot_id='ops' AND state='queued'").fetchone()[0] == 1
        )  # parked, not lost
    put(api, "usage/limits/ops", {"daily_usd": 50})
    assert claim(api, r) is not None  # raising the limit resumes it
    assert paused(api)["state"] != "paused"


def test_the_monthly_limit_and_the_company_default_block_too(api):
    r = fleet(api)
    put(api, "usage/limits", {"monthly_usd": 4})  # ops has no limit of its own
    first = start(api, r)
    finish(api, r, first, 500_000)
    post(api, "chat/ops", {"text": "More."})
    assert claim(api, r) is None
    shown = get(api, "usage/limits")["bots"]["ops"]
    assert shown["blocked"] == "monthly" and shown["source"]["monthly"] == "company" and shown["month_spent"] == 5
    put(api, "usage/limits/ops", {"monthly_usd": 100})  # its own limit wins over the default
    assert claim(api, r) is not None


def test_who_may_set_a_limit_and_what_the_default_keeps(api):
    # Subscription runs count unless the team turns that off.
    assert get(api, "usage/limits")["default"]["count_subscription"] is True  # never chosen
    assert (
        put(api, "usage/limits", {"daily_usd": 20, "count_subscription": False})["default"]["count_subscription"]
        is False
    )
    assert get(api, "usage/limits")["default"]["count_subscription"] is False  # a saved choice stays
    assert put(api, "usage/limits", {"daily_usd": 20})["default"]["count_subscription"] is False  # left out keeps it
    assert put(api, "usage/limits", {"count_subscription": True})["default"]["count_subscription"] is True
    # A computer cannot set or read limits.
    r = runner(api)
    assert api.get("/api/v2/usage/limits", headers=headers(r["token"])).status_code in (401, 403)
    assert api.put("/api/v2/usage/limits/ops", json={"daily_usd": 1}, headers=headers(r["token"])).status_code in (
        401,
        403,
    )
    put(api, "usage/limits", {"daily_usd": 3})
    put(api, "usage/pools/company", {"per_30m_usd": 10}, "ben-test")  # Ben is an administrator
    put(api, "usage/pools/groups/nope", {"per_30m_usd": 10}, expected=404)
    # Ben is an administrator: any bot, and the default.
    put(api, "usage/limits/ops", {"daily_usd": 30}, "ben-test")
    put(api, "usage/limits", {"daily_usd": 3}, "ben-test")
    as_member(api, "ben@acme.example")
    mine = get(api, "usage/limits", "ben-test")
    assert sorted(mine["bots"]) == ["cpo", "product-design"] and mine["may_edit_default"] is False
    assert mine["default"]["daily_usd"] == 3
    put(api, "usage/limits", {"daily_usd": 300}, "ben-test", expected=403)  # not the company's
    put(api, "usage/limits/ops", {"daily_usd": 1}, "ben-test", expected=403)  # not his bot
    put(api, "usage/limits/cpo", {"daily_usd": 5}, "ben-test", expected=403)  # above the company default
    put(api, "usage/limits/cpo", {"daily_usd": 2, "monthly_usd": 40}, "ben-test")  # below it; no default for monthly
    put(api, "usage/limits/cpo", {}, "ben-test")  # empty changes nothing
    assert get(api, "usage/limits", "ben-test")["bots"]["cpo"]["daily_usd"] == 2
    put(api, "usage/limits/cpo", {"daily_usd": None}, "ben-test")  # cleared, it follows the default
    assert get(api, "usage/limits", "ben-test")["bots"]["cpo"]["daily_usd"] == 3
    put(api, "usage/limits/cpo", {"daily_usd": 2}, "cara-test", expected=403)  # she sees it, does not run it
    put(api, "usage/limits/inbox", {"daily_usd": 2}, "cara-test", expected=404)  # Ana's private bot is not even there
    assert get(api, "usage/limits", "cara-test")["bots"] == {}
    for bad in ({"daily_usd": 0}, {"daily_usd": -1}, {"monthly_usd": 1e12}):
        put(api, "usage/limits/ops", bad, "ana-test", expected=422)
    # Only the owner and administrators set team and group limits.
    groups(api, "eng")
    put(api, "usage/pools/company", {"per_30m_usd": 100}, "ben-test", expected=403)
    put(api, "usage/pools/groups/eng", {"per_30m_usd": 1}, "ben-test", expected=403)
    assert get(api, "usage/limits", "ben-test")["pools"] is None
    assert get(api, "usage/limits")["pools"]["company"]["per_30m_usd"] == 10


def test_a_message_or_task_for_a_bot_over_its_limit_is_saved_and_the_sender_is_told_why_it_waits(api):
    from backend.tests.helpers.mcp import call

    r = fleet(api)
    put(api, "usage/limits/ops", {"daily_usd": 1})
    finish(api, r, start(api, r), 500_000)  # $5 against a $1 limit
    err, sent = call(api, "tico_message_send", {"to": "ops", "text": "Check the Acme invoice."})
    assert not err and sent["id"]  # saved: it runs once the limit allows
    assert sent["warning"] == (
        "ops is over its daily limit ($5.00 of $1.00); it starts again at 00:00 UTC or when a person raises the limit."
    )
    err, made = call(api, "tico_task_create", {"owner": "ops", "title": "Review the Acme invoice", "body": "Please."})
    assert not err and made["task"]["id"] and "over its daily limit" in made["warning"]
    assert paused(api)["limit"] == {**paused(api)["limit"], "period": "daily", "spent": 5, "limit": 1}
    put(api, "usage/limits/ops", {"daily_usd": 50})
    err, sent = call(api, "tico_message_send", {"to": "ops", "text": "And this."})
    assert not err and "warning" not in sent


def test_a_group_or_team_limit_holds_every_bot_it_covers_and_an_hourly_limit_set_before_counts_the_hour(api):
    from datetime import datetime
    from backend import usage_limits
    from backend.tests.helpers.mcp import call

    r = fleet(api)
    groups(api, "product")  # ops is in Product, under Engineering
    put(api, "usage/pools/groups/eng", {"daily_usd": 1})
    finish(api, r, start(api, r), 500_000)  # $5 against the group's $1
    post(api, "chat/ops", {"text": "More."})
    assert claim(api, r) is None  # the parent group's limit holds it
    assert paused(api)["focus"] == "Paused: the Engineering group is over its daily limit"
    assert "spent" not in paused(api)["limit"]  # the group's total is not every reader's
    err, sent = call(api, "tico_message_send", {"to": "ops", "text": "Check the Acme invoice."})
    assert not err and "the Engineering group is over its daily limit" in sent["warning"]
    pools = get(api, "usage/limits")["pools"]
    assert pools["groups"]["eng"]["day_spent"] == 5 and pools["groups"]["eng"]["blocked"] == "daily"
    put(api, "usage/pools/groups/eng", {"daily_usd": None})  # nothing left removes it
    with api.app.state.store.read() as c:
        assert c.execute("SELECT count(*) FROM budget_nodes WHERE team_id='eng'").fetchone()[0] == 0
    put(api, "usage/pools/company", {"weekly_usd": 4})
    assert claim(api, r) is None  # the whole team's week is spent too
    assert paused(api)["focus"] == "Paused: the team is over its weekly limit"
    put(api, "usage/pools/company", {"weekly_usd": 400})
    assert claim(api, r) is not None
    with api.app.state.store.transaction() as c:  # an hour limit set before the half hour
        usage_limits.save_own(c, "ops", {**usage_limits.own(c, "ops"), "hourly_usd": 1}, None)
    with api.app.state.store.read() as c:
        started = c.execute("SELECT min(created_at) FROM attempts WHERE bot_id='ops'").fetchone()[0]
        at = datetime.fromisoformat(started[:19]).replace(tzinfo=usage_limits.timezone.utc)
        assert usage_limits.blocked(c, "ops", at=at) == "hourly"  # the run's own hour
        assert usage_limits.blocked(c, "ops", at=at + usage_limits.timedelta(hours=1)) is None


def _jobs(api, bot="ops"):
    with api.app.state.store.read() as c:
        return [
            dict(row)
            for row in c.execute(
                """SELECT j.id, j.message_id, j.state, j.held, m.sender_id, m.refs,
                      json_extract(m.refs,'$.caused_by') AS caused_by
               FROM jobs j JOIN messages m ON m.id=j.message_id WHERE j.bot_id=?""",
                (bot,),
            )
        ]


def turn_wake(c, task_id):
    """The message of the queued run a new task's turn woke its owner bot with."""
    return c.execute(
        "SELECT j.message_id FROM jobs j JOIN messages m ON m.id=j.message_id "
        "WHERE j.state='queued' AND m.wake_key LIKE ? || ':%'",
        (task_id,),
    ).fetchone()[0]


def test_clearing_the_automatic_queue_keeps_people_running_work_and_tasks(api):
    from backend.store import H
    from backend.tests.helpers.threads_runner import make_thread, say

    r = fleet(api)
    groups(api, "product")  # ops is in Product, under Engineering
    running = start(api, r)
    post(api, "chat/ops", {"text": "Please keep this."})
    thread = make_thread(api, title="Review notes", members=["bot:ops"])
    carried = say(api, thread, "@ops please check this too")
    post(api, "tasks", {"title": "Review the patch", "body": "Look at the patch.", "owner": "bot:ops"})  # Ana's turn
    with api.app.state.store.transaction() as c:
        # A task Tico made on its own: its turn is the automatic wake.
        task = H.task_create(c, H.KEEPER, "Refresh the export", "Refresh it.", H.bot_actor("ops"), lint=False)
        row = H.task(c, task["id"])
        before = (row["status"], row["stage_id"], row["owner_id"])
        automatic = {"id": turn_wake(c, task["id"])}
    post(api, "usage/queues/purge", {"scope": "group:missing"}, expected=404)
    result = post(api, "usage/queues/purge", {"scope": "group:eng"})
    jobs = _jobs(api)
    assert result["scope"] == "group:eng" and result["cancelled"] >= 1
    assert result["kept_people"] >= 1 and result["still_running"] >= 1
    assert any(j["state"] == "queued" and str(j["sender_id"]).startswith("human:") for j in jobs)
    assert any(j["state"] == "queued" and j["caused_by"] == "human:ana" for j in jobs)
    assert any(j["state"] == "queued" and H._json(j["refs"], {}).get("carries") == carried["id"] for j in jobs)
    assert next(j for j in jobs if j["message_id"] == automatic["id"])["state"] == "canceled"
    assert all(j["state"] != "queued" or H.in_person_name(j) or j["caused_by"] == "human:ana" for j in jobs)
    with api.app.state.store.read() as c:
        row = H.task(c, task["id"])
        assert (row["status"], row["stage_id"], row["owner_id"]) == before
        assert c.execute("SELECT state FROM attempts WHERE id=?", (running["id"],)).fetchone()[0] in (
            "leased",
            "running",
        )
    again = post(api, "usage/queues/purge", {"scope": "company"}, "ben-test")  # administrators may clear too
    assert again["cancelled"] == 0
    post(api, "usage/queues/purge", {}, r["token"], expected=403)
    post(api, "usage/queues/purge", {}, running["token"], expected=403)
    as_member(api, "ben@acme.example")
    post(api, "usage/queues/purge", {"scope": "company"}, "ben-test", expected=403)


def test_clearing_a_group_leaves_other_groups_and_clears_held_jobs(api):
    from backend.store import H

    r = fleet(api)
    assign(api, r, "finance")
    ready(api, r, ["ops", "finance"])
    groups(api, "product")
    messages = {}
    for bot in ("ops", "finance"):
        with api.app.state.store.transaction() as c:
            task = H.task_create(c, H.KEEPER, "Review for " + bot, "Check it.", H.bot_actor(bot), lint=False)
            messages[bot] = turn_wake(c, task["id"])
            c.execute("UPDATE jobs SET held=1 WHERE message_id=?", (messages[bot],))
    result = post(api, "usage/queues/purge", {"scope": "group:eng"})
    assert result["cancelled"] == 1
    ours = next(j for j in _jobs(api) if j["message_id"] == messages["ops"])
    theirs = next(j for j in _jobs(api, "finance") if j["message_id"] == messages["finance"])
    assert (ours["state"], ours["held"]) == ("canceled", 0)
    assert (theirs["state"], theirs["held"]) == ("queued", 1)
    result = post(api, "usage/queues/purge", {})  # the default covers the whole team
    assert result["scope"] == "company" and result["cancelled"] == 1
    assert next(j for j in _jobs(api, "finance") if j["message_id"] == messages["finance"])["state"] == "canceled"
    assert post(api, "usage/queues/purge", {})["cancelled"] == 0


def test_a_hold_counts_live_runs_once_even_when_claims_repeat(api, monkeypatch, caplog):
    from backend import usage_limits

    r = fleet(api)
    finish(api, r, start(api, r), 500_000)
    put(api, "usage/limits/ops", {"daily_usd": 1})
    monkeypatch.setattr(usage_limits, "LOGGED", set())
    caplog.clear()
    with api.app.state.store.transaction() as c:
        c.executemany(
            "INSERT INTO attempts(id, bot_id, created_at, state) VALUES(?, ?, ?, ?)",
            [
                ("live-lease", "cpo", "2026-10-15T12:00:00Z", "leased"),
                ("live-run", "product-design", "2026-10-15T12:00:00Z", "running"),
            ],
        )
        mine = usage_limits.own(c, "ops")
        queries = []
        c.set_trace_callback(queries.append)
        try:
            assert usage_limits.blocked(c, "ops", mine=mine) == "daily"
            assert usage_limits.blocked(c, "ops", mine=mine) == "daily"
        finally:
            c.set_trace_callback(None)
    logs = [r for r in caplog.records if r.name == "backend.usage_limits"]
    assert len(logs) == 1 and logs[0].args[-3:] == (5, 1, 2)
    assert len([q for q in queries if "SELECT count(*) FROM attempts WHERE state" in q]) == 1
