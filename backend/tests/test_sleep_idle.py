"""Sleep when idle, the install's side (backend/sleep_idle.py), against a fake Tico HQ."""
import dataclasses
import json
import time

import httpx
import pytest

from backend import botops_act, releases, routines, scheduler, sleep_idle
from backend import hubdb as H
from backend.tests.test_api import api, assign, claim, get, post, ready, runner, setup_attempt  # noqa: F401

KEY = "tgw_acme_test_key"


class HQ:
    """Tico HQ's `servers/self` routes (the contract), in memory."""

    def __init__(self):
        self.hosted, self.calls, self.sleeps = True, [], []
        self.sleep_answer = (202, {"ok": True, "stopping": True})
        self.on_sleep = None

    def __call__(self, request):
        path = request.url.path.removeprefix("/v1/gateway/servers/")
        body = json.loads(request.content or b"{}")
        self.calls.append((request.method, path, body))
        assert request.headers["authorization"] == "Bearer " + KEY and request.headers["user-agent"].startswith("Tico-Client/")
        if request.method == "GET" and path == "self":
            return httpx.Response(200, json={"hosted": self.hosted, "server_id": "srv_1" if self.hosted else None,
                                             "layout": "separate" if self.hosted else None, "state": "running",
                                             "sleep": {"stopped_by": None, "wake_at": None}})
        if request.method == "POST" and path == "self/sleep":
            assert request.headers["idempotency-key"]
            self.sleeps.append(body)
            if self.on_sleep:
                self.on_sleep()
            status, answer = self.sleep_answer
            return httpx.Response(status, json=answer if status < 400 or isinstance(answer, dict) else {"error": answer})
        return httpx.Response(404, json={"error": "not_found"})


@pytest.fixture
def hq(api, monkeypatch):
    monkeypatch.setenv("TICO_GATEWAY_KEY", KEY)
    fake = HQ()
    monkeypatch.setattr(sleep_idle, "TRANSPORT", httpx.MockTransport(fake))
    clock = [time.time()]
    monkeypatch.setattr(sleep_idle, "now", lambda: clock[0])
    monkeypatch.setattr(sleep_idle, "POLICY", {"at": None, "minutes": None})
    fake.clock = clock
    service(api).started = 0                 # the server has been up longer than any setting
    system_jobs(api)                         # no system job or release hour is near, whatever the hour the test runs at
    return fake


def system_jobs(api, **on):
    """Every controlled system job off except the ones named (`key=(preset, "HH:MM")`), and the release hours off."""
    from backend import release_flow
    from backend import system_routines as SR
    config = {key: {"on": False} for key in SR.CONTROLS}
    config.update({key: {"on": True, "preset": preset, "at": at} for key, (preset, at) in on.items()})
    with api.app.state.store.transaction() as c:
        for key, value in ((SR.CONFIG_KEY, config), (release_flow.KEY, {"on": False})):
            c.execute("INSERT INTO registry_metadata VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                      (key, json.dumps(value)))


def service(api):
    return api.app.state.sleep_idle


def turn_on(api, hours=2, **more):
    state = post(api, "sleep", {"hours": hours, **more})["sleep"]
    service(api).ui_at = 0                   # the owner's own click was the last time anyone was there; long ago now
    return state


def tick(api):
    """One look. The test's own requests were a signed-in person, so nobody is there as far as the look is concerned."""
    service(api).ui_at = 0
    service(api).tick()


def add_routine(api, due, key="r1"):
    """A routine of ops's (its schedule automation, on), next due at `due`."""
    with api.app.state.store.transaction() as c:
        aid = routines.create(c, H.KEEPER, "ops", {"title": "Morning check", "cron": "0 9 * * *", "timezone": "UTC",
                                                    "text": "Look around"}, key=key)["automation"]
        c.execute("UPDATE automations SET next_due=? WHERE id=?",
                  (scheduler.stamp(sleep_idle.datetime.fromtimestamp(due, sleep_idle.timezone.utc)), aid))


def quiet_routines(api):
    """Switch off every schedule automation. Setting up a runner seeds the Librarian's built-in schedules, due at the
    next :00 or :30, so a test that expects busy() to be "" would fail within fifteen minutes of either."""
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE automations SET enabled=0 WHERE enabled=1 AND deleted_at IS NULL AND next_due IS NOT NULL")


def quiet(api, minutes=60):
    with api.app.state.store.read() as c:
        return service(api).quiet(c, minutes, sleep_idle.now())


def test_each_thing_that_keeps_the_server_up_is_named_and_stops_the_sleep(api, hq):
    at = hq.clock[0]
    assert quiet(api) == {"idle": True, "since": 0, "blocker": ""}
    service(api).ui_at = at - 600                                       # someone signed in ten minutes ago
    assert quiet(api)["idle"] is False and quiet(api)["blocker"] == ""
    service(api).ui_at = at - 2 * 3600
    assert quiet(api)["idle"] is True

    # Each step adds a blocker that comes before the last one in the order the server looks.
    add_routine(api, at + 10 * 60)
    assert quiet(api)["blocker"] == "routine_due" and quiet(api)["idle"] is False
    with api.app.state.store.transaction() as c:
        c.execute("INSERT INTO webhook_sends(id,event_id,webhook_id,type,body,state,next_attempt_at,created_at) VALUES('wh_1','we_1','ep_1','task.done','{}','ready',?,?)", (sleep_idle.stamp(at), sleep_idle.stamp(at)))    # due now
    assert quiet(api)["blocker"] == "send_pending"
    r = runner(api, "ana")
    with api.app.state.store.transaction() as c:
        c.execute("INSERT INTO runner_harness_actions(id,runner_id,harness,action,state,created_at,updated_at,requested_by) "
                  "VALUES('a1',?,?,?,?,?,?,?)", (r["runner_id"], "claude", "update", "requested", H.now(), H.now(), "human:ana"))
    assert quiet(api)["blocker"] == "runner_job"
    with api.app.state.store.transaction() as c:
        c.execute("INSERT INTO worktree_waits(id,task_id,owner,repo,generation,state,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?)",
                  ("w1", "T1", "bot:ops", "acme/shop", 1, "waiting", H.now(), H.now()))
    assert quiet(api)["blocker"] == "task_session"
    assign(api, r, "ops")
    ready(api, r, ["ops"])
    post(api, "chat/ops", {"text": "Please summarize the current work."})
    assert quiet(api)["blocker"] == "run_queued"
    claim(api, r)
    assert quiet(api)["blocker"] == "run_in_progress"


def test_a_quiet_hosted_server_asks_once_with_the_next_routine_as_wake_time(api, hq):
    at = hq.clock[0]
    wake = int(at + 3 * 3600)
    add_routine(api, wake)
    seen = {}

    def before_the_call():                   # the history already says why, and no write transaction is open
        with api.app.state.store.read() as c:
            seen["event"] = c.execute("SELECT detail FROM events WHERE action='server.sleep'").fetchone()
    hq.on_sleep = before_the_call

    state = turn_on(api, 2, runners=False)
    assert state["hosted"] and state["hours"] == 2 and state["runners"] is False and state["layout"] == "separate"
    tick(api)
    assert hq.sleeps == [{"wake_at": sleep_idle.iso(wake), "runners": False, "reason": "idle"}]
    assert json.loads(seen["event"][0])["wake_at"] == sleep_idle.iso(wake)
    last = get(api, "sleep")["sleep"]["last_request"]
    assert last["result"] == "requested" and last["wake_at"] == sleep_idle.iso(wake)

    hq.clock[0] += 14 * 60
    tick(api)
    assert len(hq.sleeps) == 1               # not again within 15 minutes
    hq.clock[0] += 2 * 60
    tick(api)
    assert len(hq.sleeps) == 2               # still running after that: asked again

    turn_on(api, 0)                          # the way out
    hq.clock[0] += 3600
    tick(api)
    assert len(hq.sleeps) == 2 and get(api, "sleep")["sleep"]["hours"] == 0


def test_never_in_rehearsal_and_never_when_not_hosted(api, hq):
    turn_on(api, 2)
    svc = service(api)
    before = len(hq.calls)
    live = svc.settings
    svc.settings = dataclasses.replace(live, rehearsal=True)
    tick(api)
    assert len(hq.calls) == before and hq.sleeps == []
    svc.settings = dataclasses.replace(live, demo=True)
    tick(api)
    assert len(hq.calls) == before
    svc.settings = live

    hq.hosted = False
    hq.clock[0] += sleep_idle.HOSTED_FOR + 1
    tick(api)                               # HQ now says this server is not Tico's: nothing is asked
    assert hq.sleeps == [] and get(api, "sleep")["sleep"]["hosted"] is False
    post(api, "sleep", {"hours": 3}, expected=409)
    assert turn_on(api, 0)["hours"] == 0     # off always works


def test_not_now_answers_are_quiet_and_a_failure_backs_off_and_reaches_health(api, hq):
    turn_on(api, 1)
    hq.sleep_answer = (409, {"error": {"code": "cooldown", "detail": "Started 3 minutes ago"}})
    tick(api)
    state = get(api, "sleep")["sleep"]
    assert len(hq.sleeps) == 1 and state["last_request"]["result"] == "cooldown" and state["last_error"] == ""
    for _ in range(3):
        tick(api)
    assert len(hq.sleeps) == 1               # no retry storm

    hq.clock[0] += 16 * 60
    hq.sleep_answer = (500, "boom")
    tick(api)
    assert len(hq.sleeps) == 2
    state = get(api, "sleep")["sleep"]
    assert state["last_error"] and state["last_request"]["result"] == "failed"
    with api.app.state.store.read() as c:
        assert "failed" in sleep_idle.health(c)["summary"]
    hq.clock[0] += 16 * 60
    tick(api)
    assert len(hq.sleeps) == 3               # the second failure waits twice as long
    hq.clock[0] += 16 * 60
    tick(api)
    assert len(hq.sleeps) == 3
    hq.clock[0] += 16 * 60
    hq.sleep_answer = (202, {"ok": True})
    tick(api)
    assert len(hq.sleeps) == 4 and get(api, "sleep")["sleep"]["last_error"] == ""
    with api.app.state.store.read() as c:
        assert sleep_idle.health(c) is None


def test_only_an_owner_or_an_admin_changes_it_and_anyone_signed_in_reads_it(api, hq):
    assert get(api, "sleep", token="cara-test")["sleep"]["hours"] == 0
    post(api, "sleep", {"hours": 2}, token="cara-test", expected=403)
    post(api, "sleep", {"hours": 25}, expected=422)
    post(api, "sleep", {}, expected=422)
    assert hq.calls == [] or all(c[1] == "self" for c in hq.calls)
    assert post(api, "sleep", {"hours": 4}, token="ben-test")["sleep"]["hours"] == 4          # an admin
    assert post(api, "sleep", {"runners": False})["sleep"]["runners"] is False                # the owner
    _, _, attempt = setup_attempt(api, "ops")
    post(api, "sleep", {"hours": 2}, token=attempt["token"], expected=403)                    # a bot
    assert get(api, "sleep", token=attempt["token"])["sleep"]["hours"] == 4
    assert botops_act.classify("POST", "sleep") == "do"                                       # BotOps for a person
    with api.app.state.store.read() as c:
        assert c.execute("SELECT count(*) FROM events WHERE action='server.sleep_set'").fetchone()[0] == 2



def test_a_daily_system_job_sets_the_wake_time_and_an_interval_job_does_not(api, hq):
    from datetime import datetime

    from backend import system_routines as SR
    zone = SR.zone()
    noon = datetime(2026, 10, 26, 12, 0, tzinfo=zone)                    # Monday noon, company time
    hq.clock[0] = noon.timestamp()
    system_jobs(api, learnings=("daily", "03:00"), kpi_asks=("15m", "09:00"))
    with api.app.state.store.transaction() as c:                         # tonight's 03:00 run already happened today
        c.execute("INSERT INTO learning_runs(id,night,status,started_at) VALUES('lr1','2026-10-26','done',?)", (H.now(),))
    with api.app.state.store.read() as c:
        assert sleep_idle.busy(c, hq.clock[0]) == ""                     # the 15-minute job never keeps the server up
        night = sleep_idle.wake_time(c, hq.clock[0])
    assert night == datetime(2026, 10, 27, 3, 0, tzinfo=zone).timestamp()

    system_jobs(api, kpi_asks=("15m", "09:00"))                          # only the interval job left
    with api.app.state.store.read() as c:
        assert sleep_idle.wake_time(c, hq.clock[0]) is None and sleep_idle.busy(c, hq.clock[0]) == ""
    system_jobs(api, still_wanted=("daily", "12:10"))                    # ten minutes away: it keeps the server up
    with api.app.state.store.transaction() as c:
        c.execute("INSERT INTO system_runs(id,job,started_at) VALUES('sr1','still_wanted',?)", (sleep_idle.stamp(hq.clock[0] - 3600),))
    with api.app.state.store.read() as c:
        assert sleep_idle.busy(c, hq.clock[0]) == "routine_due"


def test_a_send_waiting_for_a_far_retry_does_not_keep_the_server_up_but_sets_the_wake_time(api, hq):
    at = hq.clock[0]
    with api.app.state.store.transaction() as c:
        c.execute("INSERT INTO webhook_sends(id,event_id,webhook_id,type,body,state,next_attempt_at,created_at) VALUES('wh_1','we_1','ep_1','task.done','{}','ready',?,?)", (sleep_idle.stamp(at + 10 * 3600), sleep_idle.stamp(at)))
    with api.app.state.store.read() as c:
        assert sleep_idle.busy(c, at) == ""
        assert sleep_idle.wake_time(c, at) == pytest.approx(at + 10 * 3600, abs=1)
    for later, state, due in ((at + 600, "ready", True), (at + 10 * 3600, "sending", True), (at - 60, "ready", True)):
        with api.app.state.store.transaction() as c:
            c.execute("UPDATE webhook_sends SET state=?, next_attempt_at=?", (state, sleep_idle.stamp(later)))
        with api.app.state.store.read() as c:
            assert (sleep_idle.busy(c, at) == "send_pending") is due


class Cell:
    """A Tico Cloud cell's updater listener, `GET /sleep` (the idle policy) and `POST /sleep` (setup/cell_updater.py)."""

    def __init__(self):
        self.sleeps, self.answer, self.minutes, self.down = [], (202, {"state": "sleeping", "wake_at": None}), None, False

    def __call__(self, request):
        assert request.headers["authorization"] == "Bearer cell-token"
        if self.down:
            raise httpx.ConnectError("the listener is down", request=request)
        assert request.url.path == "/sleep"
        if request.method == "GET":
            return httpx.Response(200, json={"minutes": self.minutes})
        assert request.method == "POST"
        self.sleeps.append(json.loads(request.content))
        status, answer = self.answer
        return httpx.Response(status, json=answer)


@pytest.fixture
def cell(hq, monkeypatch):
    """This install is a Tico Cloud cell company: its listener answers, and HQ is not to be asked."""
    monkeypatch.setenv("TICO_CELL", "1")
    monkeypatch.setenv("TICO_UPDATER_URL", "http://10.0.0.1:9001")
    monkeypatch.setenv("TICO_UPDATER_TOKEN", "cell-token")
    fake = Cell()
    monkeypatch.setattr(releases, "TRANSPORT", httpx.MockTransport(fake))
    return fake


def listener(cell, minutes):
    """The listener's policy from now on. Its answer is kept a minute, so the next look is the first to see a change."""
    cell.minutes = minutes
    sleep_idle.POLICY.update(at=None)


def test_a_cell_company_asks_its_cell_not_hq_and_sends_no_runners(api, hq, cell):
    at = hq.clock[0]
    wake = int(at + 3 * 3600)
    add_routine(api, wake)
    state = turn_on(api, 1, runners=False)
    assert state["hosted"] and state["active"] and state["hours"] == 1
    assert (state["minutes"], state["source"], state["managed"]) == (60, "owner", True)
    tick(api)
    assert cell.sleeps == [{"wake_at": sleep_idle.iso(wake), "reason": "idle"}]
    assert hq.calls == []                    # no Gateway key, no HQ question
    last = get(api, "sleep")["sleep"]["last_request"]
    assert last["result"] == "requested" and last["wake_at"] == sleep_idle.iso(wake)


def test_a_cell_never_set_sleeps_after_fifteen_minutes_and_an_explicit_zero_is_off(api, hq, cell):
    state = get(api, "sleep")["sleep"]
    assert state["hours"] == 0 and state["active"] and state["hosted"]
    assert (state["minutes"], state["source"]) == (15, "default")
    service(api).started = hq.clock[0] - 10 * 60          # quiet for ten minutes: not yet
    tick(api)
    assert cell.sleeps == []
    hq.clock[0] += 6 * 60
    tick(api)
    assert len(cell.sleeps) == 1

    state = post(api, "sleep", {"runners": False})["sleep"]    # runners alone leave the default alone
    assert (state["minutes"], state["source"]) == (15, "default")
    state = turn_on(api, 0)                                     # the owner's explicit 0
    assert (state["minutes"], state["source"], state["active"]) == (0, "owner", False)
    hq.clock[0] += 3 * 3600
    tick(api)
    assert len(cell.sleeps) == 1


def test_the_cells_minutes_from_its_listener_win_over_the_owners_hours(api, hq, cell):
    turn_on(api, 2)
    listener(cell, 5)
    state = get(api, "sleep")["sleep"]
    assert (state["minutes"], state["source"], state["hours"]) == (5, "cell", 2)
    service(api).started = hq.clock[0] - 6 * 60
    tick(api)
    assert len(cell.sleeps) == 1


def test_a_null_listener_policy_falls_back_to_the_owners_hours(api, hq, cell):
    turn_on(api, 2)
    listener(cell, None)
    assert (get(api, "sleep")["sleep"]["minutes"], get(api, "sleep")["sleep"]["source"]) == (120, "owner")
    service(api).started = hq.clock[0] - 119 * 60
    tick(api)
    assert cell.sleeps == []
    hq.clock[0] += 2 * 60
    tick(api)
    assert len(cell.sleeps) == 1


def test_a_zero_from_the_listener_means_never_sleep_whatever_the_owner_set(api, hq, cell):
    turn_on(api, 1)
    listener(cell, 0)
    state = get(api, "sleep")["sleep"]
    assert (state["minutes"], state["source"], state["active"]) == (0, "cell", False)
    hq.clock[0] += 10 * 3600
    tick(api)
    assert cell.sleeps == []


def test_a_listener_that_is_down_counts_as_no_policy(api, hq, cell):
    cell.down = True
    state = get(api, "sleep")["sleep"]
    assert (state["minutes"], state["source"], state["managed"]) == (15, "default", True)
    turn_on(api, 3)
    state = get(api, "sleep")["sleep"]
    assert (state["minutes"], state["source"]) == (180, "owner")


def test_a_cell_looks_every_minute_and_any_other_install_every_five(api, hq, cell):
    assert sleep_idle.every() == 60
    assert sleep_idle.CELL_EVERY == 60 and sleep_idle.EVERY == 300


def test_a_cell_sleeps_after_fifteen_minutes_and_waits_only_for_a_routine_within_fifteen(api, hq, cell):
    base = int(hq.clock[0])
    service(api).started = base - 16 * 60
    add_routine(api, base + 10 * 60)                     # due in ten minutes: it keeps the cell up
    tick(api)
    assert cell.sleeps == [] and quiet(api, 15)["blocker"] == "routine_due"
    with api.app.state.store.transaction() as c:         # moved to sixteen minutes: beyond the fifteen
        c.execute("UPDATE automations SET next_due=?", (scheduler.stamp(
            sleep_idle.datetime.fromtimestamp(base + 16 * 60, sleep_idle.timezone.utc)),))
    tick(api)
    assert cell.sleeps == [{"wake_at": sleep_idle.iso(base + 16 * 60), "reason": "idle"}]


def test_a_cell_with_no_routine_near_sleeps_with_no_wake_time(api, hq, cell):
    service(api).started = hq.clock[0] - 16 * 60
    tick(api)
    assert cell.sleeps == [{"wake_at": None, "reason": "idle"}]


def test_the_view_gives_the_effective_minutes_their_source_and_that_a_cell_is_managed(api, hq, cell):
    state = get(api, "sleep")["sleep"]
    assert "hours_default" not in state
    assert (state["minutes"], state["source"], state["managed"]) == (15, "default", True)
    turn_on(api, 2)
    assert (get(api, "sleep")["sleep"]["minutes"], get(api, "sleep")["sleep"]["source"]) == (120, "owner")
    listener(cell, 30)
    state = get(api, "sleep")["sleep"]
    assert (state["minutes"], state["source"], state["hours"]) == (30, "cell", 2)


def test_a_busy_cell_says_not_now_and_is_asked_again_after_fifteen_minutes(api, hq, cell):
    turn_on(api, 1)
    cell.answer = (409, {"error": "busy"})
    tick(api)
    state = get(api, "sleep")["sleep"]
    assert len(cell.sleeps) == 1 and state["last_request"]["result"] == "busy" and state["last_error"] == ""
    hq.clock[0] += sleep_idle.SOON - 60
    tick(api)
    assert len(cell.sleeps) == 1
    hq.clock[0] += 60
    tick(api)
    assert len(cell.sleeps) == 2


def test_a_failing_cell_backs_off_like_hq_and_reaches_health(api, hq, cell):
    turn_on(api, 1)
    cell.answer = (500, {"error": "boom"})
    tick(api)
    state = get(api, "sleep")["sleep"]
    assert len(cell.sleeps) == 1 and state["last_request"]["result"] == "failed" and state["last_error"] == "updater_500"
    with api.app.state.store.read() as c:
        assert "failed" in sleep_idle.health(c)["summary"]
    hq.clock[0] += 16 * 60
    tick(api)
    assert len(cell.sleeps) == 2             # the second failure waits twice as long
    hq.clock[0] += 16 * 60
    tick(api)
    assert len(cell.sleeps) == 2
    hq.clock[0] += 16 * 60
    cell.answer = (202, {"state": "sleeping"})
    tick(api)
    assert len(cell.sleeps) == 3 and get(api, "sleep")["sleep"]["last_error"] == ""
    with api.app.state.store.read() as c:
        assert sleep_idle.health(c) is None


def test_an_online_bot_computer_keeps_a_cell_company_up_until_it_goes_quiet(api, hq, cell):
    turn_on(api, 1)
    r = runner(api, "ana")
    ready(api, r, [])                        # the computer reports in now
    quiet_routines(api)
    with api.app.state.store.read() as c:
        assert sleep_idle.busy(c, hq.clock[0]) == "computer_online"
    tick(api)
    assert cell.sleeps == [] and quiet(api)["blocker"] == "computer_online"
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE runners SET last_seen_at=?", (sleep_idle.stamp(time.time() - 300),))
    tick(api)
    assert len(cell.sleeps) == 1


@pytest.fixture
def managed_only(hq, monkeypatch):
    """TICO_UPDATES=tico with no TICO_CELL: Tico updates this install from outside, but it is not a cell. Its updater
    listener is never to be called."""
    monkeypatch.delenv("TICO_CELL", raising=False)
    monkeypatch.setenv("TICO_UPDATES", "tico")
    monkeypatch.setenv("TICO_UPDATER_URL", "http://10.0.0.1:9001")
    monkeypatch.setenv("TICO_UPDATER_TOKEN", "cell-token")
    monkeypatch.setattr(releases, "TRANSPORT", httpx.MockTransport(lambda request: pytest.fail("the updater was called")))
    return hq


def test_tico_updates_alone_is_not_a_cell_so_sleep_is_off_by_default(api, hq, managed_only):
    state = get(api, "sleep")["sleep"]
    assert (state["minutes"], state["source"], state["managed"], state["active"]) == (0, "default", False, False)
    assert sleep_idle.every() == sleep_idle.EVERY == 300
    tick(api)
    assert [call[:2] for call in hq.calls] == [("GET", "self")] and hq.sleeps == []   # only the hosted question


def test_tico_updates_alone_asks_hq_with_runners_and_no_computer_blocker(api, hq, managed_only):
    state = turn_on(api, 1, runners=False)
    assert (state["minutes"], state["source"], state["managed"], state["active"]) == (60, "owner", False, True)
    r = runner(api, "ana")
    ready(api, r, [])                        # an online bot computer: only a cell counts it as a blocker
    quiet_routines(api)
    with api.app.state.store.read() as c:
        assert sleep_idle.busy(c, hq.clock[0]) == ""
    tick(api)
    assert len(hq.sleeps) == 1 and hq.sleeps[0]["runners"] is False and hq.sleeps[0]["reason"] == "idle"
    assert get(api, "sleep")["sleep"]["last_request"]["result"] == "requested"


def test_outside_a_cell_nothing_is_defaulted_and_an_online_computer_does_not_block(api, hq):
    state = get(api, "sleep")["sleep"]
    assert (state["minutes"], state["source"], state["managed"]) == (0, "default", False)
    turn_on(api, 2)
    assert (get(api, "sleep")["sleep"]["minutes"], get(api, "sleep")["sleep"]["source"]) == (120, "owner")
    r = runner(api, "ana")
    ready(api, r, [])
    quiet_routines(api)
    with api.app.state.store.read() as c:
        assert sleep_idle.busy(c, hq.clock[0]) == ""
