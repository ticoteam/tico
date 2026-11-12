"""Spend limits (backend/usage_limits.py, docs/usage.md): a bot over a limit takes no new job and a run already going
finishes, the 80% notice comes once, raising the limit resumes the bot, and who may set one."""

from backend.tests.helpers.api import as_member, assign, claim, get, headers, post, ready, runner  # noqa: F401
from backend.tests.helpers.usage_limits import finish, fleet, groups, paused, put, start


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
