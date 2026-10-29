"""How a run lives in the hub (backend/automations.py): saved after each step that did something and resumed from
there, each effect done once; a turn that ends before its run is saved still answers it; the chain carried through a
bot's turn; the run guard; the spend cap holding turns not priced yet; schedules; the find
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
    return {**row, "state": json.loads(row["state_json"] or "{}")}


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


def test_a_burst_of_distinct_external_events_is_a_backlog_not_a_loop(api):
    aid = make(api, [{"id": "tell", "type": "action", "do": "wake_bot", "args": {"bot": "ops", "text": "Hook"}}],
               trigger={"type": "webhook", "id": "trg_1"})
    with api.app.state.store.transaction() as c:
        for n in range(70):   # an outage's backlog flushed: 70 distinct deliveries, none calls another
            automations.on_webhook(c, "trg_1", "Deploys", "custom", f"d{n}", {}, "")
    assert len(sql(api, "SELECT id FROM automation_runs WHERE automation_id=?", (aid,))) == 70
    assert get(api, f"automations/{aid}")["enabled"] is True
    assert sql(api, "SELECT id FROM events WHERE action='automation.run_guard' AND target=?", (aid,)) == []


def test_a_trigger_ref_repeating_is_a_loop_and_the_run_guard_pauses_it(api):
    aid = make(api, [{"id": "tell", "type": "action", "do": "wake_bot", "args": {"bot": "ops", "text": "Hook"}}],
               trigger={"type": "webhook", "id": "trg_1"})
    paused = "SELECT owner FROM tasks WHERE title LIKE 'Automation Test paused%'"
    before = sql(api, paused)
    with api.app.state.store.transaction() as c:
        for _ in range(70):   # the same event re-fires the automation again and again, as `fire` finds it enabled
            row = dict(c.execute("SELECT * FROM automations WHERE id=?", (aid,)).fetchone())
            if row["enabled"]:
                automations.queue(c, row, {}, "webhook:trg_1:d0")
    assert len(sql(api, "SELECT id FROM automation_runs WHERE automation_id=?", (aid,))) == automations.RUN_GUARD
    assert get(api, f"automations/{aid}")["enabled"] is False
    assert sql(api, paused) == before + [{"owner": "human:ana"}]
    assert len(sql(api, "SELECT id FROM events WHERE action='automation.run_guard' AND target=?", (aid,))) == 1


def backdate(api, aid, count, minutes_ago, ref=""):
    """`count` runs that started `minutes_ago` minutes ago, as a window that has passed (no time travel needed)."""
    started = H.shift(H.now(), minutes=-minutes_ago)
    with api.app.state.store.transaction() as c:
        version = c.execute("SELECT version FROM automations WHERE id=?", (aid,)).fetchone()[0]
        for _ in range(count):
            c.execute("INSERT INTO automation_runs(id,automation_id,version,trigger_ref,started,status,state_json,chain_json) "
                      "VALUES(?,?,?,?,?,'done','{}','[]')", ("run_" + H.new_id(), aid, version, ref, started))


def test_a_loop_with_fresh_ids_that_keeps_going_is_paused_in_its_second_window(api):
    aid = make(api, [{"id": "tell", "type": "action", "do": "wake_bot", "args": {"bot": "ops", "text": "Hook"}}],
               trigger={"type": "webhook", "id": "trg_1"})
    backdate(api, aid, automations.RUN_GUARD, 15)   # the window before: a full RUN_GUARD
    with api.app.state.store.transaction() as c:
        for n in range(70):   # a webhook that calls itself back with a new delivery id each time
            automations.on_webhook(c, "trg_1", "Deploys", "custom", f"loop{n}", {}, "")
    assert get(api, f"automations/{aid}")["enabled"] is False
    assert len(sql(api, "SELECT id FROM automation_runs WHERE automation_id=?", (aid,))) == 2 * automations.RUN_GUARD
    assert len(sql(api, "SELECT id FROM tasks WHERE body LIKE '%kept queuing runs for 20 minutes%'")) >= 1
    trip = sql(api, "SELECT detail_json FROM events WHERE action='automation.run_guard' AND target=?", (aid,))
    assert '"rule": "sustained"' in trip[0]["detail_json"]


def test_a_burst_that_is_one_window_only_is_not_sustained(api):
    aid = make(api, [{"id": "tell", "type": "action", "do": "wake_bot", "args": {"bot": "ops", "text": "Hook"}}],
               trigger={"type": "webhook", "id": "trg_1"})
    backdate(api, aid, automations.RUN_GUARD - 1, 15)   # the window before is under RUN_GUARD
    with api.app.state.store.transaction() as c:
        for n in range(70):
            automations.on_webhook(c, "trg_1", "Deploys", "custom", f"burst{n}", {}, "")
    assert get(api, f"automations/{aid}")["enabled"] is True
    assert len(sql(api, "SELECT id FROM automation_runs WHERE automation_id=?", (aid,))) == 70 + automations.RUN_GUARD - 1


def test_sixty_manual_runs_do_not_pause_the_next_unrelated_trigger(api):
    aid = make(api, [{"id": "tell", "type": "action", "do": "wake_bot", "args": {"bot": "ops", "text": "Hook"}}],
               trigger={"type": "webhook", "id": "trg_1"})
    with api.app.state.store.transaction() as c:
        row = dict(c.execute("SELECT * FROM automations WHERE id=?", (aid,)).fetchone())
        for _ in range(automations.RUN_GUARD):   # "Run now" clicked 60 times: one ref, but not one event
            automations.queue(c, row, {}, "manual", manual=True)
        assert automations.on_webhook(c, "trg_1", "Deploys", "custom", "after-runs", {}, "")
    assert get(api, f"automations/{aid}")["enabled"] is True
    assert len(sql(api, "SELECT id FROM automation_runs WHERE automation_id=?", (aid,))) == automations.RUN_GUARD + 1


def test_a_flood_of_distinct_external_events_past_the_ceiling_is_paused(api, monkeypatch):
    monkeypatch.setattr(automations, "RUN_GUARD_BURST", 70)   # a webhook whose call calls it back, with new ids each time
    aid = make(api, [{"id": "tell", "type": "action", "do": "wake_bot", "args": {"bot": "ops", "text": "Hook"}}],
               trigger={"type": "webhook", "id": "trg_1"})
    with api.app.state.store.transaction() as c:
        for n in range(80):
            automations.on_webhook(c, "trg_1", "Deploys", "custom", f"d{n}", {}, "")
    assert len(sql(api, "SELECT id FROM automation_runs WHERE automation_id=?", (aid,))) == 70
    assert get(api, f"automations/{aid}")["enabled"] is False


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
        c.execute(f"UPDATE tasks SET created=? WHERE id IN ({','.join('?' * len(ids))})", (H.now(), *ids))
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
        c.execute("UPDATE tasks SET created=? WHERE id IN (?,?)", (H.now(), *ids))
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
        c.execute("UPDATE tasks SET created=?, title='Acme batch' WHERE id IN (?,?)", (H.now(), *ids))
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
        c.execute("INSERT INTO automation_holds(ref,automation_id,run_id,amount,at) VALUES('t_old',?,'run_old',0.25,?)",
                  (aid, H.shift(H.now(), hours=-2)))
    assert post(api, f"automations/{aid}/run", {})["run"]["steps"][0]["status"] == "spend_cap"
    automations.tick(api.app.state.store, datetime.now(timezone.utc) + timedelta(hours=25))
    assert sql(api, "SELECT ref FROM automation_holds WHERE ref='t_old'") == []
    swept = sql(api, "SELECT detail_json AS detail FROM events WHERE action='automation.hold_swept' AND target=?", (aid,))
    assert len(swept) == 1 and json.loads(swept[0]["detail"])["turn"] == "t_old"


def test_a_failed_item_is_tried_again_once_then_passed_over_and_counted(api):
    bad = {"id": "gate", "type": "filter", "rules": [
        {"when": [{"field": "item.title", "op": "contains", "value": "bad"}], "then": [{"do": "post_update"}]}]}
    aid = make(api, [{**FIND, "limit": 10}, bad])
    ids = [post(api, "tasks", {"owner": "ops", "title": f"Acme batch {word}", "body": "x"})["id"]
           for word in ("bad", "good")]
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE tasks SET created=? WHERE id IN (?,?)", (H.now(), *ids))
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
    told = sql(api, "SELECT body FROM messages WHERE to_actor='bot:ops' AND kind='notice' AND body LIKE 'Scheduled occurrence%'")
    assert len(told) == 1 and "absorbed into this open task" in told[0]["body"]
    turn_tasks = sql(api, "SELECT id FROM tasks WHERE owner='bot:ops' AND title='Test'")
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
        c.execute("UPDATE automation_runs SET status='waiting',wake_at=NULL,started=?,state_json=? WHERE id=?",
                  (old, json.dumps({"threads": [], "waiting_since": recent}), rid))
    automations.tick(api.app.state.store)
    assert run_row(api, rid)["status"] == "waiting"
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE automation_runs SET state_json=? WHERE id=?",
                  (json.dumps({"threads": [], "waiting_since": old}), rid))
    automations.tick(api.app.state.store)
    assert run_row(api, rid)["status"] == "failed"
