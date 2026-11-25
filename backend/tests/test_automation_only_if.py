"""A trigger's Only if (backend/automation_only_if.py): checked on save, read as the owner just before the run starts,
a miss or a failed read skips the run with its reason (no step, no turn, no spend), and a run that goes on reads
`only_if.count` and `only_if.items`. Also the reads it leans on: tasks by a stage's name, and GET /api/v2/prs."""

import asyncio
import json

import pytest

from backend import automation_only_if
from backend.automation_flow import Invalid, check, shape_text
from backend.store import H
from backend.tests.helpers.api import get, post
from backend.tests.helpers.automation_runs import queued, run_row, sql

NOTE = {"id": "n", "type": "action", "do": "note", "args": {"text": "Seen {{only_if.count}}"}}
OPEN_TODO = "tasks?stage=To do&status=open"


def trigger(**only_if):
    return {"type": "event", "name": "x.y", "only_if": only_if}


def flow(only_if, owner="person:ana"):
    return {"name": "Test", "owner": owner, "trigger": trigger(**only_if), "steps": [NOTE]}


def make(api, owner="person:ana", **only_if):
    return post(api, "automations", flow(only_if, owner))["id"]


def run(api, aid):
    """One run of the automation, fired and drained: its row as the API shows it, and its saved state."""
    rid = queued(api, aid)
    api.app.state.store.automations.drain()
    shown = next(r for r in get(api, f"automations/{aid}/runs")["runs"] if r["id"] == rid)
    return shown, run_row(api, rid)


def task(api, title, token="ana-test", **extra):
    return post(api, "tasks", {"owner": "ops", "title": title, "body": "x", **extra}, token=token)


# ---------------------------------------------------------------------------------------------- checked on save
@pytest.mark.parametrize(
    "only_if",
    [
        {"read": "/tasks?status=open", "at_least": 1},  # a leading slash
        {"read": "https://acme.example/api/v2/tasks", "at_least": 1},  # a host
        {"read": "tasks/../events", "at_least": 1},
        {"read": "tasks/%2e%2e/events", "at_least": 1},
        {"read": "events", "at_least": 1},  # a stream
        {"read": "oauth/google/start", "at_least": 1},  # a sign-in door
        {"read": "tasks#x", "at_least": 1},
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


def test_a_good_only_if_saves_on_every_trigger_type(api):
    for only_if in (
        {"read": "tasks?stage=Release ready&status=open", "at_least": 1},
        {"read": "prs?repo=gotidy&idle_hours=48", "at_least": 1},
        {"read": "tasks?owner=ops&label=candidate", "none": True},
    ):
        check(flow(only_if))
    every = [
        {"type": "task_received", "to": "bot:ops"},
        {"type": "schedule", "cron": "0 9 * * 1-5", "tz": "UTC"},
        {"type": "event", "name": "task.created"},
    ]
    for kind in every:
        rule = {"read": OPEN_TODO, "at_least": 1}
        post(api, "automations", {**flow(rule), "trigger": {**kind, "only_if": rule}})
    assert "only_if" in shape_text()


# ---------------------------------------------------------------------------------------------- read just before a run
def test_at_least_met_runs_and_its_items_reach_the_steps(api):
    task(api, "Acme launch checklist")
    aid = make(api, read=OPEN_TODO, at_least=1)
    shown, row = run(api, aid)
    assert shown["status"] == "ok" and "reason" not in shown
    assert row["state"]["only_if"]["count"] >= 1 and row["state"]["only_if"]["ok"] is True
    seen = row["state"]["payload"]["only_if"]
    assert "Acme launch checklist" in [item.get("title") for item in seen["items"]]
    assert f"Seen {seen['count']}" in json.dumps(row["state"]["steps"])


def test_a_miss_skips_the_run_with_its_reason_no_step_no_turn_no_spend(api):
    aid = make(api, read="tasks?q=zzqxv&status=open", at_least=1)
    count = "SELECT (SELECT count(*) FROM messages) AS said, (SELECT count(*) FROM attempts) AS turns"
    before = sql(api, count)
    shown, row = run(api, aid)
    assert shown["status"] == "skipped" and shown["cost"] == 0
    assert shown["reason"] == "Only if: tasks?q=zzqxv&status=open has 0, needs at least 1"
    assert row["state"]["steps"] == [] and row["state"]["threads"] == []
    assert sql(api, count) == before  # no note, no wake, no turn
    assert get(api, f"automations/{aid}/runs")["daily"][0]["dropped"] == 1
    overview = get(api, "automations/runs")
    assert any(r.get("reason") == shown["reason"] for r in overview["runs"])


def test_none_runs_only_while_the_read_is_empty(api):
    read = "tasks?q=zzqxv&status=open"
    aid = make(api, read=read, none=True)
    assert run(api, aid)[0]["status"] == "ok"
    task(api, "Acme zzqxv checklist")
    shown, _ = run(api, aid)
    assert shown["status"] == "skipped" and shown["reason"] == f"Only if: {read} is not empty"


def test_the_read_is_the_owners_a_task_the_owner_cannot_see_is_not_counted(api):
    task(api, "Acme zzqxv review", private=True)  # Ana's and ops's; Cara cannot see it
    read = "tasks?q=zzqxv&status=open"
    caras = make(api, owner="person:cara", read=read, at_least=1)
    anas = make(api, read=read, at_least=1)
    assert run(api, caras)[0]["status"] == "skipped"
    assert run(api, anas)[0]["status"] == "ok"


def test_a_read_that_does_not_answer_in_time_skips_the_run(api, monkeypatch):
    never = asyncio.Event

    async def stuck(app, who, base, only_if):
        await never().wait()

    monkeypatch.setattr(automation_only_if, "TIMEOUT_S", 0.05)
    monkeypatch.setattr(automation_only_if, "_pages", stuck)
    aid = make(api, read=OPEN_TODO, at_least=1)
    shown, row = run(api, aid)
    assert shown["status"] == "skipped" and "did not answer" in shown["reason"]
    assert row["state"]["steps"] == []


def test_a_read_that_fails_skips_the_run_with_the_error(api):
    aid = make(api, read="tasks?colour=red", at_least=1)  # not a tasks filter: 422
    shown, _ = run(api, aid)
    assert shown["status"] == "skipped" and shown["reason"].startswith("Only if check failed: the read answered 422")


def test_a_lent_identity_only_reads(api):
    """The scope key the check rides in admits GET only: a write with it is refused before any route."""
    with api.app.state.store.read() as c:
        who = automation_only_if.owner_identity(c, api.app.state.auth, "person:ana")

    async def write():
        import httpx

        transport = httpx.ASGITransport(app=automation_only_if._lent(api.app, who))
        async with httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1") as client:
            return await client.post("/api/v2/tasks", json={"owner": "ops", "title": "No", "body": "x"})

    assert asyncio.run(write()).status_code == 403
    assert sql(api, "SELECT count(*) AS n FROM tasks WHERE title='No'")[0]["n"] == 0


def test_a_run_by_hand_is_not_checked(api):
    aid = make(api, read="tasks?q=zzqxv&status=open", at_least=1)
    done = post(api, f"automations/{aid}/run", {})["run"]
    assert done["status"] == "ok"


# ---------------------------------------------------------------------------------------------- the reads it leans on
def test_tasks_take_a_stage_by_its_name_in_any_pipeline(api):
    filed = task(api, "Acme launch checklist")
    by_name = get(api, "tasks?stage=to do&status=open")["tasks"]
    by_id = get(api, "tasks?stage=general-todo&status=open")["tasks"]
    assert filed["id"] in [t["id"] for t in by_name] and filed["id"] in [t["id"] for t in by_id]
    get(api, "tasks?stage=No such stage", expected=422)


def test_prs_lists_the_pull_requests_on_tasks_the_reader_can_see(api):
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
