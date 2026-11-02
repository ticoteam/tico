"""How a run lives in the hub (backend/automations.py): saved after each step that did something and resumed from
there, each effect done once; a turn that ends before its run is saved still answers it; the chain carried through a
bot's turn; the run guard's hold; the spend cap holding turns not priced yet; schedules; the find
watermark."""
import json
from datetime import datetime, timedelta, timezone

import pytest
import time_machine

from backend import automations
from backend.store import H
from backend.tests.test_api import api, assign, claim, get, headers, post, ready, runner  # noqa: F401
from backend.tests.test_automation_api import patch
from backend.tests.test_automation_turns import draft_then_tell, finish

EVENT = {"type": "event", "name": "x.y"}


class Stop(BaseException):
    """The server stopping mid-run: nothing in the hub catches it."""


def make(api, steps, trigger=EVENT, owner="person:ana", **extra):
    return post(api, "automations", {"name": "Test", "owner": owner, "trigger": trigger, "steps": steps, **extra})["id"]


def queued(api, aid, payload=None):
    with api.app.state.store.transaction() as c:
        row = dict(c.execute("SELECT * FROM automations WHERE id=?", (aid,)).fetchone())
        return automations.queue(c, row, payload or {}, "test")


def run_row(api, rid):
    with api.app.state.store.read() as c:
        row = dict(c.execute("SELECT * FROM automation_runs WHERE id=?", (rid,)).fetchone())
    return {**row, "saved_state": json.loads(row["saved_state"] or "{}")}


def sql(api, query, args=()):
    with api.app.state.store.read() as c:
        return [dict(r) for r in c.execute(query, args)]


def hub(api):
    return api.app.state.store.automations


# ---------------------------------------------------------------------------------------------- saved and resumed
def test_a_run_cut_off_after_an_effect_goes_on_from_its_save_and_does_not_do_it_twice(api, monkeypatch):
    aid = make(api, [{"id": "spawn", "type": "action", "do": "create_task", "args": {"title": "Acme follow up",
                                                                                     "owner": "coo"}},
                     {"id": "tell", "type": "action", "do": "wake_bot", "args": {"bot": "coo", "text": "Done"}}])
    rid = queued(api, aid)
    save = automations.HubServices.checkpoint

    def stop(self, state):
        raise Stop()
    monkeypatch.setattr(automations.HubServices, "checkpoint", stop)
    with pytest.raises(Stop):
        hub(api).drain()
    monkeypatch.setattr(automations.HubServices, "checkpoint", save)
    assert run_row(api, rid)["status"] == "running"
    automations.tick(api.app.state.store, datetime.now(timezone.utc) + timedelta(minutes=20))
    done = run_row(api, rid)
    assert done["status"] == "ok" and done["state"]["resumes"] == 1
    assert [s["id"] for s in done["state"]["steps"]] == ["spawn", "tell"]
    assert len(sql(api, "SELECT id FROM tasks WHERE title='Acme follow up'")) == 1


def test_a_run_saves_after_each_step_with_an_effect(api, monkeypatch):
    aid = make(api, [{"id": "spawn", "type": "action", "do": "create_task", "args": {"title": "Acme check"}},
                     {"id": "tell", "type": "action", "do": "wake_bot", "args": {"bot": "coo", "text": "Done"}}])
    rid = queued(api, aid)
    seen = []
    save = automations.HubServices.checkpoint

    def spy(self, state):
        save(self, state)
        seen.append([s["id"] for s in run_row(api, rid)["state"]["steps"]])
    monkeypatch.setattr(automations.HubServices, "checkpoint", spy)
    hub(api).drain()
    assert seen[:2] == [["spawn"], ["spawn", "tell"]]


def test_a_turn_that_ends_before_its_run_is_saved_still_answers_it(api, monkeypatch):
    r = runner(api)
    assign(api, r, "ops")
    ready(api, r, ["ops"])
    aid = post(api, "automations", draft_then_tell())["id"]
    start = automations.HubServices.start_turn

    def quick(self, *args):
        started = start(self, *args)
        finish(api, r, "A quick draft")   # the bot answers before the run has saved that it waits
        return started
    monkeypatch.setattr(automations.HubServices, "start_turn", quick)
    rid = queued(api, aid)
    hub(api).drain()
    done = run_row(api, rid)
    assert done["status"] == "ok", done["state"]
    assert done["state"]["steps"][0]["outputs"]["text"] == "A quick draft"
    assert sql(api, "SELECT * FROM automation_turn_answers") == [] and sql(api, "SELECT * FROM automation_holds") == []


# ---------------------------------------------------------------------------------------------- loops
def test_a_bots_turn_started_by_an_automation_cannot_start_it_again(api):
    r = runner(api)
    assign(api, r, "ops")
    ready(api, r, ["ops"])
    aid = make(api, [{"id": "work", "type": "bot_turn", "bot": "ops", "prompt": "Handle it"}],
               trigger={"type": "task_received", "to": "bot:coo"})
    post(api, f"automations/{aid}/run", {})
    attempt = claim(api, r)
    post(api, f"attempts/{attempt['id']}/started", {"thread_id": "t"}, token=r["token"])
    # In its turn the bot hands coo a task: a task coo received, the automation's own trigger.
    post(api, "tasks", {"owner": "coo", "title": "Acme hand-off", "body": "Please."}, token=attempt["token"])
    assert len(get(api, f"automations/{aid}/runs")["runs"]) == 1


def backdate(api, aid, count, *, seconds_ago):
    """`count` runs that started `seconds_ago` seconds ago, with fresh ids (no time travel needed)."""
    started = H.shift(H.now(), seconds=-seconds_ago)
    with api.app.state.store.transaction() as c:
        version = c.execute("SELECT version FROM automations WHERE id=?", (aid,)).fetchone()[0]
        for _ in range(count):
            c.execute("INSERT INTO automation_runs(id,automation_id,version,trigger_ref,started_at,status,saved_state,chain) "
                      "VALUES(?,?,?,?,?,'ok','{}','[]')", ("run_" + H.new_id(), aid, version, "", started))


def webhook_auto(api):
    return make(api, [{"id": "tell", "type": "action", "do": "wake_bot", "args": {"bot": "ops", "text": "Hook"}}],
                trigger={"type": "webhook", "id": "trg_1"})


def test_a_burst_of_distinct_external_events_is_a_backlog_not_a_loop(api):
    aid = webhook_auto(api)
    with api.app.state.store.transaction() as c:
        for n in range(70):   # an outage's backlog flushed: 70 distinct deliveries, none calls another
            automations.on_webhook(c, "trg_1", "Deploys", "custom", f"d{n}", {}, "")
    assert statuses(api, aid) == {"queued": 70}
    assert "hold" not in get(api, f"automations/{aid}")
    assert sql(api, "SELECT id FROM events WHERE action='automation.run_guard' AND target=?", (aid,)) == []


def test_a_burst_straddling_the_ten_minute_boundary_is_not_sustained(api):
    aid = webhook_auto(api)
    backdate(api, aid, 65, seconds_ago=605)   # 130 distinct deliveries within about 10 seconds, either side of
    backdate(api, aid, 65, seconds_ago=595)   # the flood window's edge: two minutes at most, never spread
    with api.app.state.store.transaction() as c:
        assert automations.on_webhook(c, "trg_1", "Deploys", "custom", "after", {}, "")
    assert statuses(api, aid) == {"ok": 130, "queued": 1}
    assert "hold" not in get(api, f"automations/{aid}")


def statuses(api, aid):
    rows = sql(api, "SELECT status, count(*) AS n FROM automation_runs WHERE automation_id=? GROUP BY status", (aid,))
    return {r["status"]: r["n"] for r in rows}


def hold_tasks(api):
    return sql(api, "SELECT id, status, owner_id FROM tasks WHERE title='Automation Test on hold' ORDER BY created_at")


def slow_drain(api, aid, minutes=16, per=8, start=datetime(2030, 1, 1, 9, 0, 5, tzinfo=timezone.utc), tag="d"):
    """A catch-up the mail syncer or a webhook sender pushes in pages: `per` distinct deliveries a minute."""
    for minute in range(minutes):
        with time_machine.travel(start + timedelta(minutes=minute), tick=False):
            with api.app.state.store.transaction() as c:
                for n in range(per):
                    assert automations.on_webhook(c, "trg_1", "Deploys", "custom", f"{tag}{minute}-{n}", {}, "")


def test_a_slow_drain_is_held_not_lost_and_release_queues_every_run(api):
    aid = webhook_auto(api)
    slow_drain(api, aid)   # 128 deliveries over 16 minutes: no rate tells this from a loop
    assert statuses(api, aid) == {"held": 128}   # every delivery has a run; the queued ones were held at the trip
    shown = get(api, f"automations/{aid}")
    assert shown["enabled"] is True and shown["hold"]["held"] == 128 and shown["hold"]["rule"] == "sustained"
    trip = sql(api, "SELECT detail FROM events WHERE action='automation.run_guard' AND target=?", (aid,))
    assert len(trip) == 1 and '"rule": "sustained"' in trip[0]["detail"]
    assert [t["owner_id"] for t in hold_tasks(api)] == ["human:ana"]   # one task a hold, not one a run
    assert hub(api).claim() is None                                  # the drain never takes a held run
    out = post(api, f"automations/{aid}/held", {"action": "release"})
    assert out["moved"] == 128 and "hold" not in out
    assert sum(statuses(api, aid).get(s, 0) for s in ("queued", "running", "ok", "failed")) == 128
    assert [t["status"] for t in hold_tasks(api)] == ["closed"]
    assert sql(api, "SELECT id FROM events WHERE action='automation.hold_released' AND target=?", (aid,))
    audit = sql(api, "SELECT after FROM audit_log WHERE object=? ORDER BY created_at DESC LIMIT 1", ("automation:" + aid,))
    assert json.loads(audit[0]["after"]) == {"hold": None, "release": 128}


def test_a_second_hold_after_a_release_is_a_new_hold_with_its_own_task(api):
    aid = webhook_auto(api)
    slow_drain(api, aid)
    with time_machine.travel(datetime(2030, 1, 1, 9, 16, 20, tzinfo=timezone.utc), tick=False):
        post(api, f"automations/{aid}/held", {"action": "release"})
    # Straight after the release the runs it let go are not counted again: the next delivery queues.
    with time_machine.travel(datetime(2030, 1, 1, 9, 16, 30, tzinfo=timezone.utc), tick=False):
        with api.app.state.store.transaction() as c:
            automations.on_webhook(c, "trg_1", "Deploys", "custom", "after-release", {}, "")
    assert "hold" not in get(api, f"automations/{aid}")
    # The loop was not fixed: sixteen more minutes of it hold it again, with a new task.
    slow_drain(api, aid, start=datetime(2030, 1, 1, 9, 17, 5, tzinfo=timezone.utc), tag="again")
    assert get(api, f"automations/{aid}")["hold"]["held"] >= 120
    assert [t["status"] for t in hold_tasks(api)] == ["closed", "open"]
    assert len(sql(api, "SELECT id FROM events WHERE action='automation.run_guard' AND target=?", (aid,))) == 2


def flood(api, count, tag="d", at=datetime(2030, 1, 1, 9, 0, 5, tzinfo=timezone.utc)):
    with time_machine.travel(at, tick=False):
        with api.app.state.store.transaction() as c:
            for n in range(count):   # distinct deliveries inside one minute
                automations.on_webhook(c, "trg_1", "Deploys", "custom", f"{tag}{n}", {}, "")


def live_hold(api, aid):
    with api.app.state.store.read() as c:
        hold = automations.hold_of(c, aid)
        row = automations._hold_row(c, aid)
    return hold and {**hold, "arrivals": row["arrivals"]}


def test_the_delivery_that_trips_a_flood_is_held_with_the_backlog_and_none_is_dropped(api):
    aid = webhook_auto(api)
    flood(api, automations.RUN_GUARD_BURST)
    assert statuses(api, aid) == {"queued": 500} and live_hold(api, aid) is None
    flood(api, 1, tag="trip")   # the 501st: the 500 queued are moved, not counted against the ceiling
    assert statuses(api, aid) == {"held": 501}
    hold = live_hold(api, aid)
    assert hold["rule"] == "flood" and hold["held"] == 501 and hold["dropped"] == 0 and hold["arrivals"] == 1


def test_a_manual_run_at_the_flood_boundary_does_not_trip_the_guard(api):
    aid = webhook_auto(api)
    flood(api, automations.RUN_GUARD_BURST)
    with time_machine.travel(datetime(2030, 1, 1, 9, 0, 6, tzinfo=timezone.utc), tick=False):
        with api.app.state.store.transaction() as c:
            row = dict(c.execute("SELECT * FROM automations WHERE id=?", (aid,)).fetchone())
            assert automations.queue(c, row, {}, "manual", manual=True)
    assert statuses(api, aid) == {"queued": 501} and live_hold(api, aid) is None


def test_a_flood_keeps_the_backlog_held_and_caps_new_arrivals_counting_the_rest_dropped(api):
    aid = webhook_auto(api)
    flood(api, 1100)   # 500 queued, then the hold: 500 more arrive held, the last 100 are dropped
    assert statuses(api, aid) == {"held": 1000}
    hold = live_hold(api, aid)
    assert hold["rule"] == "flood" and hold["arrivals"] == 500 and hold["dropped"] == 100   # none vanishes unseen
    assert sql(api, "SELECT dropped FROM automation_daily WHERE automation_id=?", (aid,))[0]["dropped"] == 100


def test_a_hold_after_a_release_moves_the_released_backlog_and_caps_only_its_own_arrivals(api):
    aid = webhook_auto(api)
    flood(api, 501)
    with time_machine.travel(datetime(2030, 1, 1, 9, 1, 0, tzinfo=timezone.utc), tick=False):
        with api.app.state.store.transaction() as c:   # released, and left queued (no request drains them)
            assert automations._unhold(c, aid, "human:ana", "release") == 501
    assert statuses(api, aid) == {"queued": 501} and live_hold(api, aid) is None
    # The loop goes on: 120 spread arrivals since the release, and the next one holds it again.
    slow_drain(api, aid, minutes=15, start=datetime(2030, 1, 1, 9, 2, 5, tzinfo=timezone.utc), tag="again")
    assert statuses(api, aid) == {"queued": 621}
    flood(api, 1, tag="trip", at=datetime(2030, 1, 1, 9, 17, 5, tzinfo=timezone.utc))
    assert statuses(api, aid) == {"held": 622}
    assert live_hold(api, aid)["arrivals"] == 1 and live_hold(api, aid)["rule"] == "sustained"
    flood(api, 500, tag="more", at=datetime(2030, 1, 1, 9, 17, 10, tzinfo=timezone.utc))
    hold = live_hold(api, aid)
    assert hold["arrivals"] == 500 and hold["dropped"] == 1 and statuses(api, aid) == {"held": 1121}


def put_on_hold(api, aid):
    with api.app.state.store.transaction() as c:
        automations._hold(c, dict(c.execute("SELECT * FROM automations WHERE id=?", (aid,)).fetchone()), "flood")


def release(api, aid):
    with api.app.state.store.transaction() as c:
        return automations._unhold(c, aid, "human:ana", "release")


def test_a_turn_that_answers_a_waiting_run_during_a_hold_leaves_it_held_with_the_answer(api):
    r = runner(api)
    assign(api, r, "ops")
    ready(api, r, ["ops"])
    aid = post(api, "automations", draft_then_tell())["id"]
    rid = queued(api, aid)
    hub(api).drain()
    assert run_row(api, rid)["status"] == "waiting"
    put_on_hold(api, aid)
    finish(api, r, "Held draft")   # its requests drain, but the drain never takes a held run
    held = run_row(api, rid)
    assert held["status"] == "held" and held["state"]["steps"][0]["outputs"]["text"] == "Held draft"
    assert release(api, aid) == 1
    hub(api).drain()
    done = run_row(api, rid)
    assert done["status"] == "ok" and [s["id"] for s in done["state"]["steps"]] == ["draft", "tell"]


def test_a_wake_that_comes_during_a_hold_ends_held_and_resumes_from_its_save_on_release(api):
    aid = make(api, [{"id": "tell", "type": "action", "do": "wake_bot", "args": {"bot": "ops", "text": "Batched"}}],
               batch={"times": ["09:30"], "tz": "UTC"})
    with time_machine.travel(datetime(2030, 1, 1, 9, 0, 5, tzinfo=timezone.utc), tick=False):
        rid = queued(api, aid)
        hub(api).drain()
    saved = run_row(api, rid)
    assert saved["status"] == "waiting" and saved["wake_at"].startswith("2030-01-01T09:30")
    put_on_hold(api, aid)
    with time_machine.travel(datetime(2030, 1, 1, 9, 31, tzinfo=timezone.utc), tick=False):
        automations.tick(api.app.state.store)
        held = run_row(api, rid)
        assert held["status"] == "held" and held["wake_at"] is None and held["state"] == saved["state"]
        assert hub(api).claim() is None
        assert release(api, aid) == 1
        hub(api).drain()
    done = run_row(api, rid)
    assert done["status"] == "ok" and [(s["id"], s["status"]) for s in done["state"]["steps"]] == [("tell", "ok")]


def test_a_run_cut_off_during_a_hold_resumes_held_not_queued(api):
    aid = webhook_auto(api)
    rid = queued(api, aid)
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE automation_runs SET status='running',claimed_at=? WHERE id=?",
                  (H.shift(H.now(), hours=-1), rid))
    put_on_hold(api, aid)
    automations.tick(api.app.state.store)
    held = run_row(api, rid)
    assert held["status"] == "held" and held["claimed_at"] is None and held["saved_state"]["resumes"] == 1
    assert release(api, aid) == 1 and run_row(api, rid)["status"] == "queued"


def test_a_hold_that_starts_during_a_step_stops_the_run_before_its_next_step(api, monkeypatch):
    aid = make(api, [{"id": "one", "type": "action", "do": "wake_bot", "args": {"bot": "ops", "text": "First"}},
                     {"id": "two", "type": "action", "do": "wake_bot", "args": {"bot": "ops", "text": "Second"}}])
    rid = queued(api, aid)
    act, done = automations.HubServices.act, []

    def acting(self, do, args, ctx):
        out = act(self, do, args, ctx)
        done.append(args["text"])
        if args["text"] == "First":
            put_on_hold(api, aid)   # the guard trips (another trigger) while step one runs
        return out
    monkeypatch.setattr(automations.HubServices, "act", acting)
    hub(api).drain()
    held = run_row(api, rid)
    assert held["status"] == "held" and held["finished_at"] is None and done == ["First"]
    assert [s["id"] for s in held["state"]["steps"]] == ["one"]
    assert release(api, aid) == 1
    hub(api).drain()
    finished = run_row(api, rid)
    assert finished["status"] == "ok" and done == ["First", "Second"]   # step one is not done twice
    assert [s["id"] for s in finished["state"]["steps"]] == ["one", "two"]


@pytest.mark.parametrize("how", ["drop", "off", "delete"])
def test_a_run_dropped_mid_step_finishes_that_step_and_runs_nothing_after_it(api, monkeypatch, how):
    aid = make(api, [{"id": "one", "type": "action", "do": "wake_bot", "args": {"bot": "ops", "text": "First"}},
                     {"id": "two", "type": "action", "do": "wake_bot", "args": {"bot": "ops", "text": "Second"}}])
    rid = queued(api, aid)
    act, done = automations.HubServices.act, []

    def acting(self, do, args, ctx):
        out = act(self, do, args, ctx)
        done.append(args["text"])
        if args["text"] == "First":   # while step one runs, the guard holds it and a person ends the hold
            if how == "delete":
                assert api.delete(f"/api/v2/automations/{aid}", headers=headers()).status_code == 200
            else:
                put_on_hold(api, aid)
                if how == "drop":
                    assert post(api, f"automations/{aid}/held", {"action": "drop"})["moved"] == 1
                else:
                    patch(api, aid, {"enabled": False})
        return out
    monkeypatch.setattr(automations.HubServices, "act", acting)
    hub(api).drain()
    run = run_row(api, rid)
    assert run["status"] == "skipped" and run["finished_at"] and run["claimed_at"] is None
    assert done == ["First"] and [s["id"] for s in run["state"]["steps"]] == ["one"]   # its step's record is kept
    assert hub(api).claim() is None


def test_a_turn_that_ends_after_its_run_was_dropped_mid_step_is_still_charged_to_it(api, monkeypatch):
    r = runner(api)
    assign(api, r, "ops")
    ready(api, r, ["ops"])
    aid = post(api, "automations", draft_then_tell())["id"]
    start = automations.HubServices.start_turn

    def dropped_then_answered(self, *args):
        started = start(self, *args)
        put_on_hold(api, aid)
        assert post(api, f"automations/{aid}/held", {"action": "drop"})["moved"] == 1
        finish(api, r, "A late draft")   # the turn ends before the run's step is saved
        return started
    monkeypatch.setattr(automations.HubServices, "start_turn", dropped_then_answered)
    rid = queued(api, aid)
    hub(api).drain()
    run = run_row(api, rid)
    priced = sql(api, "SELECT est_cost_usd FROM turns")[0]["est_cost_usd"]
    assert priced > 0 and run["status"] == "skipped" and run["claimed_at"] is None
    assert run["cost"] == pytest.approx(priced) and run["state"]["cost"] == pytest.approx(priced)
    with api.app.state.store.read() as c:
        assert automations._spent(c, aid, "") == pytest.approx(priced)   # the spend cap counts it, no hold left
    assert sql(api, "SELECT * FROM automation_turn_answers") == [] and sql(api, "SELECT * FROM automation_holds") == []
    assert [s["id"] for s in run["state"]["steps"]] == ["draft"]


def test_a_run_dropped_mid_step_whose_server_stopped_is_charged_its_turn_and_never_resumed(api, monkeypatch):
    r = runner(api)
    assign(api, r, "ops")
    ready(api, r, ["ops"])
    aid = post(api, "automations", draft_then_tell())["id"]
    start = automations.HubServices.start_turn

    def dropped_then_stopped(self, *args):
        start(self, *args)
        put_on_hold(api, aid)
        assert post(api, f"automations/{aid}/held", {"action": "drop"})["moved"] == 1
        raise Stop()   # the server stops before the step is saved
    monkeypatch.setattr(automations.HubServices, "start_turn", dropped_then_stopped)
    rid = queued(api, aid)
    with pytest.raises(Stop):
        hub(api).drain()
    assert run_row(api, rid)["status"] == "skipped" and run_row(api, rid)["claimed_at"]
    finish(api, r, "A draft nobody saved")   # parked: the run is still claimed
    assert len(sql(api, "SELECT * FROM automation_turn_answers")) == 1
    automations.tick(api.app.state.store, datetime.now(timezone.utc) + timedelta(minutes=automations.STALE_MINUTES + 5))
    run = run_row(api, rid)
    priced = sql(api, "SELECT est_cost_usd FROM turns")[0]["est_cost_usd"]
    assert priced > 0 and run["status"] == "skipped" and run["claimed_at"] is None
    assert run["cost"] == pytest.approx(priced) and run["state"].get("resumes") is None
    assert sql(api, "SELECT * FROM automation_turn_answers") == [] and sql(api, "SELECT * FROM automation_holds") == []
    assert hub(api).claim() is None


def test_a_run_put_on_hold_mid_step_finishes_the_step_and_is_not_queued_again(api, monkeypatch):
    aid = webhook_auto(api)
    rid = queued(api, aid)
    advance = automations.E.advance

    def held_meanwhile(flow, state, services):
        put_on_hold(api, aid)   # the guard trips while this step runs; another step is ready after it
        return {**state, "status": "running", "threads": [{**state["threads"][0], "state": automations.E.READY}]}
    monkeypatch.setattr(automations.E, "advance", held_meanwhile)
    hub(api).drain()
    held = run_row(api, rid)
    assert held["status"] == "held" and held["finished_at"] is None and held["claimed_at"] is None
    monkeypatch.setattr(automations.E, "advance", advance)
    assert release(api, aid) == 1
    hub(api).drain()
    assert run_row(api, rid)["status"] == "ok"


def test_dropping_held_runs_skips_them_clears_the_hold_and_closes_its_task(api):
    aid = webhook_auto(api)
    slow_drain(api, aid)
    out = post(api, f"automations/{aid}/held", {"action": "drop"})
    assert out["moved"] == 128 and "hold" not in out and out["enabled"] is True
    assert statuses(api, aid) == {"skipped": 128}
    assert [t["status"] for t in hold_tasks(api)] == ["closed"]
    post(api, f"automations/{aid}/held", {"action": "drop"}, expected=409)   # nothing is held now


def test_turning_it_off_while_held_skips_the_held_runs_and_clears_the_hold(api):
    aid = webhook_auto(api)
    slow_drain(api, aid)
    off = patch(api, aid, {"enabled": False})
    assert off["enabled"] is False and "hold" not in off
    assert statuses(api, aid) == {"skipped": 128}
    assert [t["status"] for t in hold_tasks(api)] == ["closed"]


def test_a_manual_run_while_held_is_refused_and_queues_nothing(api):
    aid = webhook_auto(api)
    slow_drain(api, aid)
    before = live_hold(api, aid)
    r = api.post(f"/api/v2/automations/{aid}/run", json={}, headers=headers())
    assert r.status_code == 409 and "on hold" in r.text
    assert statuses(api, aid) == {"held": 128} and live_hold(api, aid) == before   # the hold is intact


def test_only_someone_who_may_change_it_releases_or_drops_its_held_runs(api):
    from backend.tests.test_automation_api import as_bot
    aid = webhook_auto(api)
    slow_drain(api, aid)
    peer = as_bot(api, "finance")
    for action in ("release", "drop"):
        post(api, f"automations/{aid}/held", {"action": action}, peer, expected=403)
    assert statuses(api, aid) == {"held": 128} and get(api, f"automations/{aid}")["hold"]["held"] == 128
    post(api, f"automations/{aid}/held", {"action": "pause"}, expected=422)


def test_seventy_comments_on_one_task_do_not_hold_the_automation(api):
    aid = make(api, [{"id": "tell", "type": "action", "do": "wake_bot", "args": {"bot": "ops", "text": "Comment"}}],
               trigger={"type": "event", "name": "task.commented"})
    with api.app.state.store.transaction() as c:
        for n in range(70):   # distinct comment events, one task: one ref, but distinct events
            automations.on_event(c, "task.commented", "task_1", lambda n=n: {"comment_id": f"c{n}"}, "human:ana")
    assert statuses(api, aid) == {"queued": 70}
    assert "hold" not in get(api, f"automations/{aid}")


def test_a_hundred_thirty_manual_runs_do_not_hold_it_by_themselves(api):
    aid = webhook_auto(api)
    with api.app.state.store.transaction() as c:
        row = dict(c.execute("SELECT * FROM automations WHERE id=?", (aid,)).fetchone())
        for _ in range(130):   # "Run now" clicked 130 times in a minute: no spread, under the flood ceiling
            automations.queue(c, row, {}, "manual", manual=True)
        assert automations.on_webhook(c, "trg_1", "Deploys", "custom", "after-runs", {}, "")
    assert statuses(api, aid) == {"queued": 131}
    assert "hold" not in get(api, f"automations/{aid}")


# ---------------------------------------------------------------------------------------------- the spend cap
def test_a_turn_not_priced_yet_holds_its_usual_cost_against_the_cap(api):
    turn = {"type": "bot_turn", "bot": "ops", "prompt": "Look"}
    aid = make(api, [{"id": "one", **turn}, {"id": "two", **turn}], spend_cap={"per_30m": 0.2})
    run = post(api, f"automations/{aid}/run", {})["run"]
    assert [(s["id"], s["status"]) for s in run["steps"]] == [("one", "ok"), ("two", "spend_cap")]
    assert [h["amount"] for h in sql(api, "SELECT amount FROM automation_holds")] == [automations.TURN_COST]


# ---------------------------------------------------------------------------------------------- schedules
def test_a_rename_keeps_the_schedules_due_time(api):
    aid = make(api, [{"id": "n", "type": "action", "do": "wake_bot", "args": {"bot": "ops"}}],
               trigger={"type": "schedule", "cron": "0 9 * * *", "tz": "UTC"})
    automations.tick(api.app.state.store, datetime(2030, 1, 1, 10, 0, tzinfo=timezone.utc))
    due = get(api, f"automations/{aid}")["next_run"]
    assert due == "2030-01-02T09:00:00.000000Z"
    assert patch(api, aid, {"name": "Renamed"})["next_run"] == due


def test_a_schedules_filters_hold_its_run(api):
    with time_machine.travel(datetime(2030, 1, 1, 10, 0, tzinfo=timezone.utc), tick=False):
        aid = make(api, [{"id": "n", "type": "action", "do": "wake_bot", "args": {"bot": "ops"}}],
                   trigger={"type": "schedule", "cron": "0 9 * * *", "tz": "UTC",
                            "filters": [{"field": "schedule.cron", "op": "contains", "value": "never"}]})
    store = api.app.state.store
    automations.tick(store, datetime(2030, 1, 1, 10, 0, tzinfo=timezone.utc))
    automations.tick(store, datetime(2030, 1, 2, 9, 0, 30, tzinfo=timezone.utc))
    seen = get(api, f"automations/{aid}/runs")
    assert seen["runs"] == [] and seen["daily"][0]["dropped"] == 1


def test_a_sample_the_filters_drop_is_dropped_not_run(api):
    aid = make(api, [{"id": "n", "type": "action", "do": "wake_bot", "args": {"bot": "ops"}}],
               trigger={**EVENT, "filters": [{"field": "mail.from", "op": "contains", "value": "acme.example"}]})
    assert post(api, f"automations/{aid}/run", {"input": {"mail": {"from": "x@other.example"}}}) == {
        "run": None, "dropped": True}
    seen = get(api, f"automations/{aid}/runs")
    assert seen["runs"] == [] and seen["daily"][0]["dropped"] == 1
    assert post(api, f"automations/{aid}/run", {"input": {"mail": {"from": "ana@acme.example"}}})["run"]["id"]


# ---------------------------------------------------------------------------------------------- the find watermark
FIND = {"id": "look", "type": "find", "source": "tasks", "query": "Acme batch", "limit": 1}


def test_items_with_the_same_time_are_all_found_one_page_at_a_time(api):
    aid = make(api, [FIND])
    ids = [post(api, "tasks", {"owner": "ops", "title": f"Acme batch {n}", "body": "x"})["id"] for n in range(3)]
    with api.app.state.store.transaction() as c:
        c.execute(f"UPDATE tasks SET created_at=? WHERE id IN ({','.join('?' * len(ids))})", (H.now(), *ids))
    counts = [post(api, f"automations/{aid}/run", {})["run"]["steps"][0]["outputs"]["count"] for _ in range(4)]
    assert counts == [1, 1, 1, 0]


def test_a_stale_pr_find_starts_before_its_creation_and_other_finds_do_not(api):
    """A find starts at its automation's creation, except a stale-PR find, whose PRs were idle before it was made."""
    stale = make(api, [{"id": "stale", "type": "find", "source": "prs", "query": "", "idle_hours": 24, "limit": 20}])
    plain = make(api, [FIND])
    marks = {r["id"]: r["found_since"] for r in sql(api, "SELECT id, found_since FROM automations WHERE id IN (?,?)",
                                                    (stale, plain))}
    assert marks[stale] == "" and marks[plain] not in ("", None)


def test_the_watermark_moves_only_once_an_item_is_finished(api, monkeypatch):
    aid = make(api, [FIND, {"id": "tell", "type": "action", "do": "wake_bot", "args": {"bot": "ops", "text": "x"}}])
    post(api, "tasks", {"owner": "ops", "title": "Acme batch one", "body": "x"})
    before = sql(api, "SELECT found_since FROM automations WHERE id=?", (aid,))[0]["found_since"]
    queued(api, aid)

    def stop(self, state):
        raise Stop()
    monkeypatch.setattr(automations.HubServices, "checkpoint", stop)
    with pytest.raises(Stop):   # cut off right after the find
        hub(api).drain()
    assert sql(api, "SELECT found_since FROM automations WHERE id=?", (aid,))[0]["found_since"] == before


# ---------------------------------------------------------------------------------------------- review 2
def test_a_resumed_run_gets_back_the_turn_it_started_and_its_own_hold_never_caps_it(api, monkeypatch):
    aid = make(api, [{"id": "look", "type": "bot_turn", "bot": "ops", "prompt": "Look"},
                     {"id": "tell", "type": "action", "do": "wake_bot", "args": {"bot": "coo", "text": "Done"}}],
               spend_cap={"per_30m": 0.2})
    rid = queued(api, aid)
    save = automations.HubServices.checkpoint

    def stop(self, state):
        raise Stop()
    monkeypatch.setattr(automations.HubServices, "checkpoint", stop)
    with pytest.raises(Stop):   # cut off after the turn started, before the save
        hub(api).drain()
    monkeypatch.setattr(automations.HubServices, "checkpoint", save)
    automations.tick(api.app.state.store, datetime.now(timezone.utc) + timedelta(minutes=20))
    done = run_row(api, rid)
    assert [(s["id"], s["status"]) for s in done["state"]["steps"]] == [("look", "ok"), ("tell", "ok")]
    assert len(sql(api, "SELECT id FROM tasks WHERE title = 'Test'")) == 1


def test_a_turn_is_titled_with_exactly_its_automations_name(api):
    r = runner(api)
    assign(api, r, "ops")
    ready(api, r, ["ops"])
    aid = post(api, "automations", {"name": "Morning check", "owner": "person:ana", "trigger": EVENT,
                                    "steps": [{"id": "brief", "type": "bot_turn", "bot": "ops", "prompt": "Brief"}]})["id"]
    post(api, f"automations/{aid}/run", {})
    assert sql(api, "SELECT title FROM tasks WHERE title LIKE 'Morning check%'") == [{"title": "Morning check"}]


def test_each_item_of_a_find_starts_its_own_turn(api):
    r = runner(api)
    assign(api, r, "ops")
    ready(api, r, ["ops"])
    aid = make(api, [{**FIND, "limit": 10}, {"id": "answer", "type": "bot_turn", "bot": "ops", "prompt": "Look"}])
    ids = [post(api, "tasks", {"owner": "ops", "title": f"Acme batch {word}", "body": "x"})["id"]
           for word in ("one", "two")]
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE tasks SET created_at=? WHERE id IN (?,?)", (H.now(), *ids))
    run = post(api, f"automations/{aid}/run", {})["run"]
    looks = [s for s in run["steps"] if s["id"] == "answer"]
    assert [s["status"] for s in looks] == ["ok", "ok"]   # the second item is not refused as a duplicate of the first
    titles = [t["title"] for t in sql(api, "SELECT title FROM tasks WHERE title LIKE 'Test · %' ORDER BY title")]
    assert titles == ["Test · Acme batch one", "Test · Acme batch two"]


def test_items_that_share_a_title_each_start_their_own_turn(api):
    r = runner(api)
    assign(api, r, "ops")
    ready(api, r, ["ops"])
    aid = make(api, [{**FIND, "limit": 10}, {"id": "answer", "type": "bot_turn", "bot": "ops", "prompt": "Look"}])
    ids = [post(api, "tasks", {"owner": "ops", "title": f"Acme batch {word}", "body": "x"})["id"]
           for word in ("one", "two")]
    with api.app.state.store.transaction() as c:
        # Both items now carry one title, so only the turns' own identity tells them apart.
        c.execute("UPDATE tasks SET created_at=?, title='Acme batch' WHERE id IN (?,?)", (H.now(), *ids))
    run = post(api, f"automations/{aid}/run", {})["run"]
    looks = [s for s in run["steps"] if s["id"] == "answer"]
    assert [s["status"] for s in looks] == ["ok", "ok"]
    assert len(sql(api, "SELECT id FROM tasks WHERE title = 'Test · Acme batch'")) == 2


def test_a_turn_moving_its_own_task_does_not_start_its_automation_again(api):
    r = runner(api)
    assign(api, r, "ops")
    ready(api, r, ["ops"])
    aid = make(api, [{"id": "work", "type": "bot_turn", "bot": "ops", "prompt": "Handle it"}],
               trigger={"type": "event", "name": "task.state_changed"})
    post(api, f"automations/{aid}/run", {})
    finish(api, r)   # the turn ends, and the task its run started changes state
    runs = get(api, f"automations/{aid}/runs")["runs"]
    assert [run["trigger_ref"] for run in runs] == ["manual"], [run["trigger_ref"] for run in runs]


def test_two_runs_of_one_automation_each_get_their_own_turn(api):
    """A second run's turn is not refused as a duplicate of the first while the first is open."""
    aid = make(api, [{"id": "look", "type": "bot_turn", "bot": "ops", "prompt": "Look"}])
    runs = [queued(api, aid), queued(api, aid)]
    hub(api).drain()
    assert [run_row(api, rid)["state"]["steps"][0]["status"] for rid in runs] == ["ok", "ok"]
    assert len(sql(api, "SELECT id FROM tasks WHERE title='Test'")) == 2   # a turn's title is its automation's name


def test_a_hold_counts_until_its_turn_is_priced_and_a_lost_one_is_swept_after_a_day(api):
    turn = {"id": "look", "type": "bot_turn", "bot": "ops", "prompt": "Look"}
    aid = make(api, [turn], spend_cap={"per_30m": 0.2})
    with api.app.state.store.transaction() as c:   # a turn started two hours ago and still going
        c.execute("INSERT INTO automation_holds(ref,automation_id,run_id,amount,created_at) VALUES('t_old',?,'run_old',0.25,?)",
                  (aid, H.shift(H.now(), hours=-2)))
    assert post(api, f"automations/{aid}/run", {})["run"]["steps"][0]["status"] == "spend_cap"
    automations.tick(api.app.state.store, datetime.now(timezone.utc) + timedelta(hours=25))
    assert sql(api, "SELECT ref FROM automation_holds WHERE ref='t_old'") == []
    swept = sql(api, "SELECT detail AS detail FROM events WHERE action='automation.hold_swept' AND target=?", (aid,))
    assert len(swept) == 1 and json.loads(swept[0]["detail"])["turn"] == "t_old"


def test_a_failed_item_is_tried_again_once_then_passed_over_and_counted(api):
    bad = {"id": "gate", "type": "filter", "rules": [
        {"when": [{"field": "item.title", "op": "contains", "value": "bad"}], "then": [{"do": "post_update"}]}]}
    aid = make(api, [{**FIND, "limit": 10}, bad])
    ids = [post(api, "tasks", {"owner": "ops", "title": f"Acme batch {word}", "body": "x"})["id"]
           for word in ("bad", "good")]
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE tasks SET created_at=? WHERE id IN (?,?)", (H.now(), *ids))
    counts = [post(api, f"automations/{aid}/run", {})["run"]["steps"][0]["outputs"]["count"] for _ in range(3)]
    assert counts == [2, 1, 0]   # the good one once; the bad one twice, then passed over
    assert get(api, f"automations/{aid}/runs")["daily"][0]["dropped"] == 1


def test_an_automation_that_finds_runs_one_at_a_time(api):
    aid = make(api, [FIND])
    first = queued(api, aid)
    assert first and queued(api, aid) is None   # the next occurrence while the first is unfinished
    assert get(api, f"automations/{aid}/runs")["daily"][0]["dropped"] == 1
    hub(api).drain()
    assert queued(api, aid)


def test_a_schedule_absorbs_an_occurrence_while_its_last_task_is_open(api):
    """CONTRACT section 4 (Overlap): the routine's rule, for a new schedule as for a converted routine. An occurrence while
    the task its last run opened is unfinished starts no run; it counts as dropped and the owner bot is told. Once that
    task is closed, the next runs."""
    aid = make(api, [{"id": "turn", "type": "bot_turn", "bot": "ops", "prompt": "Sweep"}],
               trigger={"type": "schedule", "cron": "30 * * * *", "tz": "UTC"}, owner="bot:ops")
    assert queued(api, aid)
    hub(api).drain()
    assert queued(api, aid) is None
    assert get(api, f"automations/{aid}/runs")["daily"][0]["dropped"] == 1
    told = sql(api, "SELECT body FROM messages WHERE recipient_id='bot:ops' AND kind='notice' AND body LIKE 'Scheduled occurrence%'")
    assert len(told) == 1 and "absorbed into this open task" in told[0]["body"]
    turn_tasks = sql(api, "SELECT id FROM tasks WHERE owner_id='bot:ops' AND title='Test'")
    assert len(turn_tasks) == 1
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE tasks SET status='closed' WHERE id=?", (turn_tasks[0]["id"],))
    assert queued(api, aid)


def test_one_slack_message_starts_one_run_of_an_automation(api):
    """A channel message offered once may come back as an `@Tico` event: the second offer starts nothing."""
    aid = make(api, [{"id": "note", "type": "action", "do": "note", "args": {"text": "Seen"}}])
    with api.app.state.store.transaction() as c:
        row = dict(c.execute("SELECT * FROM automations WHERE id=?", (aid,)).fetchone())
        assert automations.queue(c, row, {}, "slack:C1:1.0")
        assert automations.queue(c, row, {}, "slack:C1:1.0") is None
        assert automations.queue(c, row, {}, "slack:C1:2.0")


def test_a_turn_has_a_day_to_answer_from_when_its_run_began_waiting(api):
    rid = queued(api, make(api, []))
    old, recent = H.shift(H.now(), days=-3), H.shift(H.now(), hours=-1)
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE automation_runs SET status='waiting',wake_at=NULL,started_at=?,saved_state=? WHERE id=?",
                  (old, json.dumps({"threads": [], "waiting_since": recent}), rid))
    automations.tick(api.app.state.store)
    assert run_row(api, rid)["status"] == "waiting"
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE automation_runs SET saved_state=? WHERE id=?",
                  (json.dumps({"threads": [], "waiting_since": old}), rid))
    automations.tick(api.app.state.store)
    assert run_row(api, rid)["status"] == "failed"
