"""A trigger's Only if (backend/automation_only_if.py): checked on save (one of its sources, with that source's filters),
counted as whoever last set it just before the run starts, a miss or a check that cannot be read skips the run with
its reason (no step, no turn, no spend), and a run that goes on reads `only_if.count` and `only_if.items`. Also the
reads it shares: tasks by a stage's name, and GET /api/v2/prs."""

import json
from datetime import datetime, timedelta, timezone

import pytest

from backend import automations
from backend.automation_flow import Invalid, check, shape_text
from backend.store import H
from backend.tests.helpers.api import get, post
from backend.tests.helpers.automation_api import patch
from backend.tests.helpers.automation_runs import queued, run_row, sql

NOTE = {"id": "n", "type": "action", "do": "note", "args": {"text": "Seen {{only_if.count}}"}}
OPEN_TODO = "tasks?stage=To do&status=open"
MISSING = "tasks?q=zzqxv&status=open"  # no task is titled so until a test adds one


class Stop(BaseException):
    """The server stopping mid-run: nothing in the hub catches it."""


def trigger(**only_if):
    return {"type": "event", "name": "x.y", "only_if": only_if}


def flow(only_if, owner="person:ana", steps=(NOTE,)):
    return {"name": "Test", "owner": owner, "trigger": trigger(**only_if), "steps": list(steps)}


def make(api, owner="person:ana", token="ana-test", steps=(NOTE,), event="x.y", **only_if):
    body = flow(only_if, owner, steps)
    return post(api, "automations", {**body, "trigger": {**body["trigger"], "name": event}}, token)["id"]


def run(api, aid):
    """One run of the automation, fired and drained: its row as the API shows it, and its saved state."""
    rid = queued(api, aid)
    api.app.state.store.automations.drain()
    shown = next(r for r in get(api, f"automations/{aid}/runs")["runs"] if r["id"] == rid)
    return shown, run_row(api, rid)


def task(api, title, token="ana-test", **extra):
    return post(api, "tasks", {"owner": "ops", "title": title, "body": "x", **extra}, token=token)


def saved_only_if(api, aid):
    return json.loads(sql(api, "SELECT flow FROM automations WHERE id=?", (aid,))[0]["flow"])["trigger"]["only_if"]


# ---------------------------------------------------------------------------------------------- checked on save
@pytest.mark.parametrize(
    "only_if",
    [
        {"read": "/tasks?status=open", "at_least": 1},  # a leading slash
        {"read": "https://acme.example/api/v2/tasks", "at_least": 1},  # a host
        {"read": "tasks/../events", "at_least": 1},
        {"read": "events", "at_least": 1},  # not a source
        {"read": "automations", "at_least": 1},
        {"read": "threads/dm/bot:ops", "none": True},
        {"read": "tasks?colour=red", "at_least": 1},  # not a tasks filter
        {"read": "prs?stage=x", "at_least": 1},  # a tasks filter, not a prs one
        {"read": "tasks?status=maybe", "at_least": 1},
        {"read": "tasks?status=open&status=done", "at_least": 1},  # twice
        {"read": "tasks?label=", "at_least": 1},  # empty: it would widen to every task
        {"read": "prs?idle_hours=soon", "at_least": 1},
        {"read": "", "at_least": 1},
        {"read": "tasks", "at_least": 1, "none": True},  # both rules
        {"read": "tasks"},  # neither
        {"read": "tasks", "at_least": 0},
        {"read": "tasks", "at_least": 1001},
        {"read": "tasks", "at_least": True},
        {"read": "tasks", "none": False},
        {"read": "tasks", "at_least": 1, "write": "x"},
    ],
)
def test_a_bad_only_if_is_refused_on_save(only_if):
    with pytest.raises(Invalid):
        check(flow(only_if))


def test_a_good_only_if_saves_on_every_trigger_but_listening(api):
    for only_if in (
        {"read": "tasks?stage=Release ready&status=open", "at_least": 1},
        {"read": "prs?repo=gotidy&idle_hours=48", "at_least": 1},
        {"read": "tasks?owner=ops&label=candidate&lane=product", "none": True},
        {"read": "tasks", "at_least": 3},
    ):
        check(flow(only_if))
    every = [
        {"type": "task_received", "to": "bot:ops"},
        {"type": "schedule", "cron": "0 9 * * 1-5", "tz": "UTC"},
        {"type": "event", "name": "task.created"},
    ]
    rule = {"read": OPEN_TODO, "at_least": 1}
    for kind in every:
        post(api, "automations", {**flow(rule), "trigger": {**kind, "only_if": rule}})
    listening = {"type": "listening", "sources": [], "only_if": rule}
    with pytest.raises(Invalid, match="listening"):
        check({**flow(rule), "trigger": listening})
    assert "only_if" in shape_text()


# ---------------------------------------------------------------------------------------------- read just before a run
def test_at_least_met_runs_its_items_reach_the_steps_and_its_record_keeps_no_items(api):
    task(api, "Acme launch checklist")
    aid = make(api, read=OPEN_TODO, at_least=1)
    shown, row = run(api, aid)
    assert shown["status"] == "ok" and "reason" not in shown
    record = row["state"]["only_if"]
    assert record["count"] >= 1 and record["ok"] is True and "items" not in record
    seen = row["state"]["payload"]["only_if"]
    assert "Acme launch checklist" in [item.get("title") for item in seen["items"]]
    assert f"Seen {seen['count']}" in json.dumps(row["state"]["steps"])
    assert "Acme launch checklist" not in json.dumps(get(api, f"automations/{aid}/runs"))


def test_a_miss_skips_the_run_with_its_reason_no_step_no_turn_no_spend(api):
    aid = make(api, read=MISSING, at_least=1)
    count = "SELECT (SELECT count(*) FROM messages) AS said, (SELECT count(*) FROM attempts) AS turns"
    before = sql(api, count)
    shown, row = run(api, aid)
    assert shown["status"] == "skipped" and shown["cost"] == 0
    assert shown["reason"] == f"Only if: {MISSING} has 0, needs at least 1"
    assert row["state"]["steps"] == [] and row["state"]["threads"] == []
    assert sql(api, count) == before  # no note, no wake, no turn
    assert get(api, f"automations/{aid}/runs")["daily"][0]["dropped"] == 1
    overview = get(api, "automations/runs")
    assert any(r.get("reason") == shown["reason"] for r in overview["runs"])


def test_none_runs_only_while_the_read_is_empty(api):
    aid = make(api, read=MISSING, none=True)
    assert run(api, aid)[0]["status"] == "ok"
    task(api, "Acme zzqxv checklist")
    shown, _ = run(api, aid)
    assert shown["status"] == "skipped" and shown["reason"] == f"Only if: {MISSING} is not empty"


def test_a_check_that_cannot_be_read_skips_the_run_with_why(api):
    aid = make(api, read="tasks?stage=No such stage", at_least=1)
    shown, _ = run(api, aid)
    assert shown["status"] == "skipped" and shown["reason"] == "Only if check failed: there is no stage No such stage"


def test_a_run_by_hand_is_not_checked(api):
    aid = make(api, read=MISSING, at_least=1)
    done = post(api, f"automations/{aid}/run", {})["run"]
    assert done["status"] == "ok"


# ---------------------------------------------------------------------------------------------- whose access it reads with
def test_the_check_reads_as_whoever_set_it_and_a_change_to_it_rebinds_it(api):
    task(api, "Acme zzqxv review", private=True)  # Ana's and ops's; Cara cannot see it
    caras = make(api, owner="person:cara", token="cara-test", read=MISSING, at_least=1)
    assert saved_only_if(api, caras)["set_by"] == "human:cara"
    shown, _ = run(api, caras)
    assert shown["status"] == "skipped" and shown["reason"] == f"Only if: {MISSING} has 0, needs at least 1"
    # A rename keeps who it reads as; a set_by a client sends is never kept.
    patch(api, caras, {"name": "Renamed"})
    rule = {"read": MISSING, "at_least": 1, "set_by": "human:ana"}
    patch(api, caras, {"trigger": {"type": "event", "name": "x.y", "only_if": rule}})
    assert saved_only_if(api, caras)["set_by"] == "human:cara"
    assert run(api, caras)[0]["status"] == "skipped"
    # Ana changes the check: it reads as Ana now, who sees the private task.
    patch(
        api, caras, {"trigger": {"type": "event", "name": "x.y", "only_if": {"read": "tasks?q=zzqxv", "at_least": 1}}}
    )
    assert saved_only_if(api, caras)["set_by"] == "human:ana"
    assert run(api, caras)[0]["status"] == "ok"


# ---------------------------------------------------------------------------------------------- a real run
def test_a_real_event_run_that_is_skipped_keeps_its_payload(api):
    aid = make(api, event="task.created", read=MISSING, at_least=1)
    filed = task(api, "Acme kickoff")  # fires task.created, as a real event starts a run
    api.app.state.store.automations.drain()
    runs = get(api, f"automations/{aid}/runs")["runs"]
    assert [r["status"] for r in runs] == ["skipped"]
    state = run_row(api, runs[0]["id"])["state"]
    assert filed["id"] in json.dumps(state["payload"]) and state["payload"]["only_if"] == {"count": 0, "items": []}


def test_a_passing_checks_items_reach_the_bot_turns_prompt(api, monkeypatch):
    prompts, start = [], automations.HubServices.start_turn

    def spy(self, who, prompt, ctx, wait):
        prompts.append(prompt)
        return start(self, who, prompt, ctx, wait)

    monkeypatch.setattr(automations.HubServices, "start_turn", spy)
    task(api, "Acme zzqxv checklist")
    turn = {"id": "look", "type": "bot_turn", "bot": "ops", "prompt": "Ready ({{only_if.count}}): {{only_if.items}}"}
    aid = make(api, steps=[turn], read=MISSING, at_least=1)
    shown, _ = run(api, aid)
    assert shown["status"] == "ok" and len(prompts) == 1
    assert "Acme zzqxv checklist" in prompts[0]


def test_a_passing_check_is_saved_before_the_first_step_so_a_resumed_run_is_never_skipped(api, monkeypatch):
    task(api, "Acme zzqxv checklist")
    steps = [
        {"id": "look", "type": "bot_turn", "bot": "ops", "prompt": "Look"},
        {"id": "tell", "type": "action", "do": "wake_bot", "args": {"bot": "assistant", "text": "Done"}},
    ]
    aid = make(api, steps=steps, read=MISSING, at_least=1)
    rid = queued(api, aid)
    save = automations.HubServices.checkpoint

    def stop(self, state):
        raise Stop()

    monkeypatch.setattr(automations.HubServices, "checkpoint", stop)
    with pytest.raises(Stop):  # cut off after the turn started, before the step's save
        api.app.state.store.automations.drain()
    assert run_row(api, rid)["state"]["only_if"]["ok"] is True
    with api.app.state.store.transaction() as c:  # the condition no longer holds
        c.execute("UPDATE tasks SET status='closed' WHERE title='Acme zzqxv checklist'")
    monkeypatch.setattr(automations.HubServices, "checkpoint", save)
    automations.tick(api.app.state.store, datetime.now(timezone.utc) + timedelta(minutes=20))
    done = run_row(api, rid)
    assert done["status"] != "skipped"
    assert [(s["id"], s["status"]) for s in done["state"]["steps"]] == [("look", "ok"), ("tell", "ok")]


# ---------------------------------------------------------------------------------------------- the reads it shares
def test_tasks_take_a_stage_by_its_name_in_any_pipeline(api):
    filed = task(api, "Acme launch checklist")
    by_name = get(api, "tasks?stage=to do&status=open")["tasks"]
    by_id = get(api, "tasks?stage=general-todo&status=open")["tasks"]
    assert filed["id"] in [t["id"] for t in by_name] and filed["id"] in [t["id"] for t in by_id]
    by_priority = get(api, "tasks?stage=to do&status=open&sort=priority")["tasks"]
    assert filed["id"] in [t["id"] for t in by_priority]
    get(api, "tasks?stage=No such stage", expected=422)


def test_prs_lists_the_pull_requests_on_tasks_the_reader_can_see_and_only_if_counts_them(api):
    seen = task(api, "Acme deploy")["id"]
    hidden = task(api, "Acme secret", private=True)["id"]
    now = H.now()
    links = [
        ("idle", seen, "open", H.shift(now, hours=-50), "acme/gotidy"),
        ("fresh", seen, "open", now, "acme/gotidy"),
        ("merged", seen, "merged", H.shift(now, hours=-50), "acme/gotidy"),
        ("other", seen, "open", H.shift(now, hours=-50), "acme/web"),
        ("secret", hidden, "open", H.shift(now, hours=-50), "acme/gotidy"),
    ]
    with api.app.state.store.transaction() as c:
        for number, (name, tid, state, activity, repo) in enumerate(links, 1):
            c.execute(
                "INSERT INTO task_links(id,task_id,kind,url,title,state,created_at,activity_at,repo,number) "
                "VALUES(?,?,?,?,?,?,?,?,?,?)",
                (f"link_{name}", tid, "pr", f"https://github.com/{repo}/pull/{number}", name, state, now, activity)
                + (repo, number),
            )

    def ids(path, token="ana-test"):
        return [p["id"] for p in get(api, path, token=token)["prs"]]

    assert ids("prs?repo=gotidy&idle_hours=48") == ["link_idle", "link_secret"]
    assert ids("prs?repo=acme/gotidy&idle_hours=48", token="cara-test") == ["link_idle"]
    assert sorted(ids("prs?state=merged")) == ["link_merged"]
    page = get(api, "prs?limit=2")
    assert len(page["prs"]) == 2 and page["next_offset"] == 2
    get(api, "prs?repository=gotidy", expected=422)
    # The same rows, counted as Cara: only the one on a task she can see.
    caras = make(api, owner="person:cara", token="cara-test", read="prs?repo=gotidy&idle_hours=48", at_least=2)
    shown, row = run(api, caras)
    assert shown["reason"] == "Only if: prs?repo=gotidy&idle_hours=48 has 1, needs at least 2"
