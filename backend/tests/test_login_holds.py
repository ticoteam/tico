"""A usage limit holds the provider login, survives restarts, and lets one probe turn through per hold window."""
from datetime import datetime, timezone

from backend import login_holds
from backend.store import H
from backend.tests.test_api import api, assign, claim, expire, get, headers, post, ready, runner

CLAUDE = "You've hit your session limit · resets 2:40am (UTC)"


def test_a_reset_in_words_is_honored_and_a_past_or_unreadable_one_falls_back_to_the_backoff():
    hit = datetime(2026, 10, 19, 1, 46, 47, tzinfo=timezone.utc)
    assert login_holds.reset_from_text(CLAUDE, hit) == datetime(2026, 10, 19, 2, 40, tzinfo=timezone.utc)
    assert login_holds.reset_from_text("resets Oct 24, 9am (UTC)", hit) == datetime(2026, 10, 24, 9, tzinfo=timezone.utc)
    # Just past (it names 1:40 at 1:46), an unknown zone, or no time at all: no reset.
    assert login_holds.reset_from_text("resets 1:40am (UTC)", hit) is None
    assert login_holds.reset_from_text("resets 2:40am (Mars/Base)", hit) is None
    assert login_holds.reset_from_text("Quota exceeded. Check your plan and billing details.", hit) is None
    # Never a hold of no time, and a weekly reset days away is kept as it is.
    assert login_holds.believable("2026-10-19T01:00:00Z", hit) is None
    assert login_holds.believable("2026-10-23T09:00:00Z", hit)
    assert login_holds.believable("2026-11-19T09:00:00Z", hit) is None


def limit(api, machine, attempt, errors=(CLAUDE,), **extra):
    post(api, f"attempts/{attempt['id']}/started", {"thread_id": "limit-" + attempt["id"]}, machine["token"])
    events = [{"seq": n, "kind": "error", "payload": {"error": text, "limit": True}} for n, text in enumerate(errors, 1)]
    post(api, f"attempts/{attempt['id']}/events", {"events": events}, machine["token"])
    return post(api, f"attempts/{attempt['id']}/complete", {"outcome": "failed", "last_seq": len(events), "limited": True,
                                                           "text": "", **extra}, machine["token"])


def done(api, machine, attempt):
    post(api, f"attempts/{attempt['id']}/started", {"thread_id": "ok-" + attempt["id"]}, machine["token"])
    post(api, f"attempts/{attempt['id']}/complete", {"outcome": "completed", "last_seq": 0, "text": "Done."},
         machine["token"])


def at(monkeypatch, when, api=None):
    """The clock reads `when`; the test computer has been reporting in all along, not waking from a silence."""
    monkeypatch.setattr(H, "now", lambda: when)
    if api is not None:
        with api.app.state.store.transaction() as c:
            c.execute("UPDATE runners SET last_seen=?,awake_since=NULL", (when,))


def test_thirteen_queued_jobs_after_a_limit_start_one_probe_at_the_reset_not_thirteen_turns(api, monkeypatch):
    # The shape of Oct 19 02:25Z: a bot's queued tasks all waited on one spent login and all started together,
    # 15 minutes before the reset the provider had named.
    at(monkeypatch, "2026-10-19T01:46:47.000000Z")
    machine = runner(api)
    assign(api, machine, "ops")
    assign(api, machine, "finance")
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE runners SET capacity=16 WHERE id=?", (machine["runner_id"],))
    ready(api, machine, ["ops", "finance"])
    for n in range(12):
        post(api, "tasks", {"owner": "ops", "title": f"Review change {n}", "body": "Please review it."})
    first = claim(api, machine)
    assert limit(api, machine, first)["outcome"] == "failed"
    # Another bot signed in with the same login on this computer queues work too: 13 jobs wait on one login.
    post(api, "tasks", {"owner": "finance", "title": "Close the month", "body": "Please do it."})
    with api.app.state.store.read() as c:
        hold = login_holds.get(c, machine["runner_id"], "", "fake")
        assert hold["until"].startswith("2026-10-19T02:40:00") and hold["reset"]
        assert "resumes at 02:40 UTC when it resets" in H.status(c, "ops")["focus"]
    at(monkeypatch, "2026-10-19T02:25:15.000000Z", api)
    ready(api, machine, ["ops", "finance"])
    assert claim(api, machine) is None                      # Dev 198's half hour has passed; the login's reset has not
    at(monkeypatch, "2026-10-19T02:40:01.000000Z", api)
    ready(api, machine, ["ops", "finance"])
    probe = claim(api, machine)
    assert probe and probe["bot"] == "ops"
    assert claim(api, machine) is None                      # one probe per login per window; the other jobs wait
    done(api, machine, probe)
    with api.app.state.store.read() as c:
        assert not login_holds.get(c, machine["runner_id"], "", "fake")
    assert claim(api, machine)                              # the login works again: the queue moves


def test_a_hold_survives_a_runner_restart_and_a_lost_probe_is_retried_as_the_same_job(api, monkeypatch):
    at(monkeypatch, "2026-10-19T01:46:47.000000Z")
    machine = runner(api)
    assign(api, machine, "ops")
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE runners SET capacity=8 WHERE id=?", (machine["runner_id"],))
    ready(api, machine, ["ops"])
    for n in range(3):
        post(api, "tasks", {"owner": "ops", "title": f"Task {n}", "body": "Please do it."})
    first = claim(api, machine)
    limit(api, machine, first, errors=("Quota exceeded. Check your plan and billing details.",))
    with api.app.state.store.read() as c:
        hold = login_holds.get(c, machine["runner_id"], "", "fake")
    # No reset given: the first limit holds 30 minutes, saved on the server, so a runner that restarts (and forgets
    # everything it knew) still gets nothing on the login before then.
    assert hold["until"].startswith("2026-10-19T02:16:47") and not hold["reset"]
    # The computer's config carries it, so a restarted runner keeps the login out of its fallback choice too.
    assert get(api, "config", machine["token"])["login_holds"] == [{"profile": "", "runtime": "fake", "until": hold["until"]}]
    at(monkeypatch, "2026-10-19T02:16:00.000000Z", api)
    ready(api, machine, ["ops"])
    assert claim(api, machine) is None
    at(monkeypatch, "2026-10-19T02:17:00.000000Z", api)
    ready(api, machine, ["ops"])
    probe = claim(api, machine)
    # The runner restarts before the probe begins: its lease lapses and the job goes back in the queue. Only that job
    # may start on the login again; a second job would be a second turn in the same window.
    expire(api, probe["id"])
    ready(api, machine, ["ops"])
    again = claim(api, machine)
    assert again and again["job_id"] == probe["job_id"]
    assert claim(api, machine) is None
    # It hits the limit again: the second in a row holds an hour.
    limit(api, machine, again, errors=("Quota exceeded. Check your plan and billing details.",))
    with api.app.state.store.read() as c:
        hold = login_holds.get(c, machine["runner_id"], "", "fake")
        assert hold["streak"] == 2 and hold["until"].startswith("2026-10-19T03:17:00")
        assert "API quota or billing limit" in H.status(c, "ops")["focus"]
    # Health names the provider whose login is held, not just the bot's own.
    issue = next(i for i in api.get("/api/status", headers=headers()).json()["health_issues"] if i.get("bot") == "ops")
    assert issue["detail"].startswith("fake API quota or billing limit; queued work waits until 2026-10-19 03:17 UTC")


def test_one_hold_covers_every_bot_on_the_login_and_a_persons_message_still_gets_one_try(api, monkeypatch):
    at(monkeypatch, "2026-10-19T01:46:47.000000Z")
    machine = runner(api)
    assign(api, machine, "ops")
    assign(api, machine, "finance")
    ready(api, machine, ["ops", "finance"])
    post(api, "tasks", {"owner": "ops", "title": "First", "body": "Please do it."})
    limit(api, machine, claim(api, machine))
    post(api, "tasks", {"owner": "finance", "title": "Close the month", "body": "Please do it."})
    ready(api, machine, ["ops", "finance"])
    assert claim(api, machine) is None                      # finance signs in with the same login on this computer
    at(monkeypatch, "2026-10-19T01:48:00.000000Z", api)
    post(api, "chat/finance", {"text": "I renewed the plan, try again."})
    retry = claim(api, machine)
    assert retry and retry["bot"] == "finance"
