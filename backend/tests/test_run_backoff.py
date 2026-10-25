"""Failed runs wait, then stop; a run that starts too many runs is held; a provider limit in any error is the limit's."""
from datetime import datetime, timezone

from backend import health_monitor, login_holds, run_backoff, turn_budgets
from backend.store import H
from backend.tests.test_api import api, assign, claim, get, post, ready, runner  # noqa: F401

WRAPPED = ("Prompt is too long · automatic compaction failed: You've hit your weekly limit · "
           "resets Oct 26, 8pm (UTC)")
TOO_BIG = "API Error: 400 too many total text bytes: 32000440 > 32000000"


def at(monkeypatch, api, when):
    """The clock reads `when`, and the test computer has been reporting in all along."""
    monkeypatch.setattr(H, "now", lambda: when)
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE runners SET last_seen=?,awake_since=NULL", (when,))


def fail(api, machine, attempt, error, summary="", **extra):
    """An older runner's failed run: the error as an event with no limit flag, `limited` not set."""
    post(api, f"attempts/{attempt['id']}/started", {"thread_id": "t-" + attempt["id"]}, machine["token"])
    events = [{"seq": 1, "kind": "error", "payload": {"error": error}}]
    post(api, f"attempts/{attempt['id']}/events", {"events": events}, machine["token"])
    return post(api, f"attempts/{attempt['id']}/complete",
                {"outcome": "failed", "last_seq": 1, "text": summary, **extra}, machine["token"])


def complete(api, machine, attempt):
    post(api, f"attempts/{attempt['id']}/started", {"thread_id": "ok-" + attempt["id"]}, machine["token"])
    post(api, f"attempts/{attempt['id']}/complete", {"outcome": "completed", "last_seq": 0, "text": "Done."},
         machine["token"])


def setup(api, monkeypatch, tasks=8, when="2026-10-24T12:00:00.000000Z"):
    at(monkeypatch, api, when)
    machine = runner(api)
    assign(api, machine, "ops")
    ready(api, machine, ["ops"])
    for n in range(tasks):
        post(api, "tasks", {"owner": "ops", "title": f"Review change {n}", "body": "Please review it."})
    return machine


def test_a_weekly_limit_wrapped_in_a_prompt_error_holds_until_its_reset_even_from_a_runner_that_did_not_flag_it(
        api, monkeypatch):
    machine = setup(api, monkeypatch, tasks=2)
    first = claim(api, machine)
    fail(api, machine, first, WRAPPED, summary=WRAPPED)
    with api.app.state.store.read() as c:
        hold = login_holds.get(c, machine["runner_id"], "", "fake")
        assert hold["until"].startswith("2026-10-26T20:00:00") and hold["reset"]
        assert H.status(c, "ops")["state"] == "limited"
        assert run_backoff.get(c, "ops").get("fails") in (None, 0)       # a limit is its own wait, never a failed run
    at(monkeypatch, api, "2026-10-24T12:10:00.000000Z")
    ready(api, machine, ["ops"])
    assert claim(api, machine) is None                                   # not 10,000 runs an hour: none until Oct 26


def test_a_prompt_too_large_to_send_starts_a_fresh_session_and_five_fast_failures_hold_the_bot_until_released(
        api, monkeypatch):
    machine = setup(api, monkeypatch)
    now = "2026-10-24T12:00:00.000000Z"
    for fails in range(1, 6):
        attempt = claim(api, machine)
        assert attempt, f"run {fails} should have started"
        done = fail(api, machine, attempt, TOO_BIG)
        assert done["outcome"] == "failed"
        with api.app.state.store.read() as c:
            if fails == 1:
                # The conversation's epoch moved: the next run starts a new provider session, as a Clear does.
                assert c.execute("SELECT epoch FROM session_epochs").fetchone()["epoch"] == 1
            state = run_backoff.get(c, "ops")
        assert state["fails"] == fails and state["error_class"] == run_backoff.PROMPT_TOO_LONG
        if fails < 5:
            wait = run_backoff.wait_for(fails)
            assert wait == 15 * 2 ** (fails - 1)
            at(monkeypatch, api, H.shift(now, seconds=wait - 1))
            ready(api, machine, ["ops"])
            assert claim(api, machine) is None                           # still pausing after the failed run
            if fails == 1:
                # The pages see the wait beside the bot's state, which a back-off leaves alone.
                line = get(api, "status?bot=ops")["status"]
                assert line["backoff"] and line.get("stopped") is None and line["state"] != "paused"
            now = H.shift(now, seconds=wait + 1)
            at(monkeypatch, api, now)
            ready(api, machine, ["ops"])
    # Held: nothing starts before the first try is due, a person's message included, and the sender is told why.
    at(monkeypatch, api, H.shift(now, seconds=run_backoff.PROBE_FIRST - 1))
    ready(api, machine, ["ops"])
    assert claim(api, machine) is None
    said = post(api, "chat/ops", {"text": "Are you there?"})
    assert "Tico stopped ops" in said["warning"] and "release" in said["warning"]
    assert claim(api, machine) is None
    with api.app.state.store.read() as c:
        assert H.status(c, "ops") is not None
        task = c.execute("SELECT title,owner,status FROM tasks WHERE title LIKE '%failed runs in a row'").fetchone()
        assert task["owner"] == "human:ana" and task["status"] in H.ACTIVE_STATUSES
        found = [f for f in health_monitor.findings(c, api.app.state.store.settings) if f["check_id"] == "run_hold"]
        assert len(found) == 1 and "ops" in found[0]["title"]
    # Only someone who manages the bot releases it; one action clears the hold and its Needs-you item.
    post(api, "bots/ops/release", {}, token="cara-test", expected=403)
    assert post(api, "bots/ops/release", {})["released"]["run_hold"] is True
    resumed = claim(api, machine)
    assert resumed
    complete(api, machine, resumed)
    with api.app.state.store.read() as c:
        assert run_backoff.get(c, "ops")["fails"] == 0
        assert c.execute("SELECT status FROM tasks WHERE title LIKE '%failed runs in a row'").fetchone()["status"] == "closed"


def hold_bot(api, machine, monkeypatch):
    """Five fast failures in a row, each after the wait the one before it set. Answers the time of the last one."""
    now = "2026-10-24T12:00:00.000000Z"
    for fails in range(1, 6):
        at(monkeypatch, api, now)
        ready(api, machine, ["ops"])
        fail(api, machine, claim(api, machine), TOO_BIG)
        now = H.shift(now, seconds=run_backoff.wait_for(fails) + 1)
    with api.app.state.store.read() as c:
        assert run_backoff.get(c, "ops")["held_at"]
        return run_backoff.get(c, "ops")["held_at"]


def tick(api, machine, monkeypatch, when):
    at(monkeypatch, api, when)
    ready(api, machine, ["ops"])


def test_a_held_bot_takes_one_try_when_it_is_due_and_a_failed_try_doubles_the_gap_up_to_eight_hours(api, monkeypatch):
    machine = setup(api, monkeypatch, tasks=12)
    held = hold_bot(api, machine, monkeypatch)
    items = "SELECT count(*) FROM tasks WHERE title LIKE '%failed runs in a row'"
    with api.app.state.store.read() as c:
        assert run_backoff.get(c, "ops")["wait_until"] == H.shift(held, seconds=run_backoff.PROBE_FIRST)
        assert c.execute(items).fetchone()[0] == 1
    tick(api, machine, monkeypatch, H.shift(held, seconds=run_backoff.PROBE_FIRST - 1))
    assert claim(api, machine) is None                                   # not yet
    gap, when = run_backoff.PROBE_FIRST, H.shift(held, seconds=run_backoff.PROBE_FIRST)
    for fails in range(6, 14):
        tick(api, machine, monkeypatch, when)
        probe = claim(api, machine)
        assert probe, f"the try after {fails - 1} failures is due"
        with api.app.state.store.read() as c:
            assert run_backoff.get(c, "ops")["wait_until"] > when        # taken: the next try is already out
        assert claim(api, machine) is None                               # a second claim in the window gets nothing
        fail(api, machine, probe, TOO_BIG)
        gap = min(gap * 2, run_backoff.MAX_WAIT)
        with api.app.state.store.read() as c:
            state = run_backoff.get(c, "ops")
            assert state["held_at"] == held and state["fails"] == fails
            assert state["wait_until"] == H.shift(when, seconds=gap)
            assert c.execute(items).fetchone()[0] == 1                   # one Needs-you item, never a second
        tick(api, machine, monkeypatch, H.shift(when, seconds=gap - 1))
        assert claim(api, machine) is None
        when = H.shift(when, seconds=gap)
    assert gap == run_backoff.MAX_WAIT == 8 * 3600


def test_a_try_that_completes_releases_the_bot_and_closes_its_item_and_a_person_can_release_mid_try(api, monkeypatch):
    machine = setup(api, monkeypatch, tasks=8)
    held = hold_bot(api, machine, monkeypatch)
    post(api, "chat/ops", {"text": "Are you there?"})
    tick(api, machine, monkeypatch, H.shift(held, seconds=run_backoff.PROBE_FIRST + 1))
    probe = claim(api, machine)
    assert probe and probe["message"]["from_actor"] == "human:ana"       # the person's message rides the first try
    complete(api, machine, probe)
    with api.app.state.store.read() as c:
        state = run_backoff.get(c, "ops")
        assert (state["held_at"], state["wait_until"], state["fails"]) == (None, None, 0)
        item = c.execute("SELECT status FROM tasks WHERE title LIKE '%failed runs in a row'").fetchone()
        assert item["status"] == "closed"
        assert c.execute("SELECT 1 FROM events WHERE action='bot.run_recovered'").fetchone()
    assert claim(api, machine)                                           # the rest of the queue runs again


def test_releasing_a_held_bot_while_its_try_is_out_starts_the_count_over_and_a_slow_failure_changes_nothing(
        api, monkeypatch):
    machine = setup(api, monkeypatch, tasks=8)
    held = hold_bot(api, machine, monkeypatch)
    due = H.shift(held, seconds=run_backoff.PROBE_FIRST)
    tick(api, machine, monkeypatch, due)
    slow = claim(api, machine)
    post(api, f"attempts/{slow['id']}/started", {"thread_id": "slow"}, machine["token"])
    tick(api, machine, monkeypatch, H.shift(due, seconds=run_backoff.FAST + 5))
    post(api, f"attempts/{slow['id']}/complete", {"outcome": "failed", "last_seq": 0, "text": "Gave up."},
         machine["token"])
    with api.app.state.store.read() as c:
        state = run_backoff.get(c, "ops")
        assert state["held_at"] == held and state["fails"] == 5          # a failure after real work counts for nothing
        assert state["wait_until"] > due
    tick(api, machine, monkeypatch, H.shift(held, hours=2))
    probe = claim(api, machine)
    assert probe
    assert post(api, "bots/ops/release", {})["released"]["run_hold"] is True      # a person acts while the try is out
    fail(api, machine, probe, TOO_BIG)
    with api.app.state.store.read() as c:
        state = run_backoff.get(c, "ops")
        assert state["held_at"] is None and state["fails"] == 1           # an ordinary first failure now
        assert state["wait_until"] == H.shift(H.now(), seconds=run_backoff.FIRST_WAIT)
        assert run_backoff.refusal(c, "ops") == "backoff"


def test_a_bot_over_its_run_budget_is_held_but_a_persons_message_still_runs_until_it_is_released(api, monkeypatch):
    machine = setup(api, monkeypatch, tasks=12)
    budget = get(api, "bots/ops/budget")
    assert (budget["turns_per_hour"], budget["turns_per_day"]) == (turn_budgets.DEFAULTS["turns_per_hour"], turn_budgets.DEFAULTS["turns_per_day"]) and budget["held"] is None
    get(api, "bots/ops/budget", token="cara-test", expected=403)
    post(api, "bots/ops/budget", {"turns_per_hour": 3, "expected_revision": budget["revision"]}, token="cara-test",
         expected=403)
    set_ = post(api, "bots/ops/budget", {"turns_per_hour": 3, "expected_revision": budget["revision"]})
    assert set_["turns_per_hour"] == 3 and set_["own"] == {"turns_per_hour": 3, "turns_per_day": None}
    for _ in range(3):
        complete(api, machine, claim(api, machine))
    assert claim(api, machine) is None                                   # the fourth task run is the runaway
    held = get(api, "bots/ops/budget")["held"]
    assert held["words"] == ("Tico stopped ops: 3 runs in the last hour. This looks like a runaway loop, not normal "
                             "work. Check its last runs, then release it or raise its budget.")
    with api.app.state.store.read() as c:
        assert c.execute("SELECT count(*) FROM jobs WHERE bot='ops' AND state='queued'").fetchone()[0] >= 3  # kept
        assert c.execute("SELECT status FROM tasks WHERE title LIKE '%runs in the last hour'").fetchone()
    post(api, "chat/ops", {"text": "Please look at this now."})
    person = claim(api, machine)
    assert person, "a person's message is never refused by the budget"
    complete(api, machine, person)
    assert claim(api, machine) is None
    assert post(api, "bots/ops/release", {})["released"]["budget"] is True
    at(monkeypatch, api, "2026-10-24T12:00:05.000000Z")                  # the count starts from the release
    ready(api, machine, ["ops"])
    again = claim(api, machine)
    assert again                                                         # the held queue starts after the release
    complete(api, machine, again)
    # Raising the budget above the runs that held the bot lets it go as well.
    for _ in range(2):
        complete(api, machine, claim(api, machine))
    assert claim(api, machine) is None and get(api, "bots/ops/budget")["held"]
    revision = get(api, "bots/ops/budget")["revision"]
    freed = post(api, "bots/ops/budget", {"turns_per_hour": 50, "expected_revision": revision})
    assert freed["released"] is True and freed["held"] is None


def test_a_persons_message_runs_through_the_wait_and_its_failure_counts(api, monkeypatch):
    machine = setup(api, monkeypatch, tasks=2)
    fail(api, machine, claim(api, machine), TOO_BIG)
    ready(api, machine, ["ops"])
    assert claim(api, machine) is None                                   # the bot's own work waits out the 15 s
    at(monkeypatch, api, "2026-10-24T12:00:05.000000Z")
    post(api, "chat/ops", {"text": "Are you there?"})
    ready(api, machine, ["ops"])
    mine = claim(api, machine)
    assert mine and mine["message"]["from_actor"] == "human:ana"         # the person's retry does not wait
    fail(api, machine, mine, TOO_BIG)
    with api.app.state.store.read() as c:
        assert run_backoff.get(c, "ops")["fails"] == 2
    at(monkeypatch, api, "2026-10-24T12:00:10.000000Z")
    ready(api, machine, ["ops"])
    assert claim(api, machine) is None                                   # re-queued, the message now waits too


def test_raising_the_team_budget_lets_go_a_bot_that_follows_it(api, monkeypatch):
    machine = setup(api, monkeypatch, tasks=6)
    post(api, "settings/turn-budgets", {"turns_per_hour": 2})
    for _ in range(2):
        complete(api, machine, claim(api, machine))
    assert claim(api, machine) is None and get(api, "bots/ops/budget")["held"]
    assert post(api, "settings/turn-budgets", {"turns_per_hour": 50})["released"] == ["ops"]
    ready(api, machine, ["ops"])
    assert claim(api, machine) and get(api, "bots/ops/budget")["held"] is None
