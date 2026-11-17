"""One answer to "why can't this run?": for each hold, the claim, Health, the task card, the status row, the "Saved -
waiting" label and a sender's warning say the same thing as holds.why (backend/holds.py)."""

import json
from dataclasses import dataclass
from typing import Any

import pytest

from backend import claim_holds, execution, holds, login_holds, start_holds, task_wakes, views
from backend.store import H
from backend.tests.helpers.api import assign, claim, get, post, ready, runner

NOW = "2026-10-24T12:00:00.000000Z"
LATER = "2026-10-24T13:00:00.000000Z"


def _metadata(c, key, value):
    c.execute(
        "INSERT INTO registry_metadata(key, value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (key, json.dumps(value)),
    )


def _bot_state(state):
    return lambda api, c, rig: c.execute("UPDATE bots SET state=? WHERE id='ops'", (state,))


def _run_hold(api, c, rig):
    c.execute(
        "INSERT INTO run_backoff(bot,fails,wait_until,error_class,last_failed_at,held_at) VALUES('ops',5,?,?,?,?)",
        (LATER, "other", NOW, NOW),
    )


def _budget(api, c, rig):
    c.execute("INSERT INTO bot_budget(bot,held_at,window,runs,budget) VALUES('ops',?, 'hour', 300, 300)", (NOW,))


def _usage_limit(api, c, rig):
    _metadata(
        c,
        login_holds.key(rig["runner_id"], "", "fake"),
        {"runner_id": rig["runner_id"], "profile": "", "runtime": "fake", "since": NOW, "until": LATER, "reset": LATER},
    )
    H.status_set(c, H.KEEPER, "ops", state="limited", focus="Usage limit")


def _cant_start(api, c, rig):
    review = H.task_create(c, H.KEEPER, "Find why ops's runs can't start", "Why.", "human:ana", lint=False)
    H.event(c, H.KEEPER, start_holds.EVENT, rig["task_id"], {"bot": "ops", "review": review["id"], "reason": "x"})


def _task_quarantine(api, c, rig):
    c.execute(
        "INSERT INTO quarantine_holds(bot_id,task_id,reason,refusal,created_at) VALUES('ops',?,'refusals','x',?)",
        (rig["task_id"], NOW),
    )


def _claim_held(api, c, rig):
    claim_holds.hold(c, api.app.state.store.settings, rig["job_id"], "RuntimeError")


def _handed_back(api, c, rig):
    pass  # handed back through the runner's own call below (`via_runner`)


def _backoff(api, c, rig):
    c.execute(
        "INSERT INTO run_backoff(bot,fails,wait_until,error_class,last_failed_at) VALUES('ops',1,?,?,?)",
        (LATER, "other", NOW),
    )


SKIP = object()  # this page does not show the kind (holds.py says which pages show which)


@dataclass
class Case:
    kind: str
    setup: Any
    health: Any = SKIP  # execution.bot_refusal: the claim's own rule, as Health reads it
    card: Any = None  # the task's Paused cause
    stopped: Any = None  # the status row's `stopped`
    label: Any = None  # a phrase of the task's "Saved - waiting" label
    warned: bool = False  # a sender to the bot gets a warning
    claimed: bool = False  # the claim hands the task's run out


CASES = [
    Case(holds.PAUSED, _bot_state("paused"), card=task_wakes.STATE_CAUSES["paused"], label="bot paused"),
    Case(holds.QUARANTINED, _bot_state("quarantined"), card="is quarantined", label="bot quarantined"),
    Case(
        holds.COMPANY_PAUSED,
        lambda api, c, rig: _metadata(c, "company_pause", {"paused_by": "human:ana", "paused_at": NOW}),
        health="company_paused",
        card=task_wakes.COMPANY_PAUSED,
        label="all bots are paused",
    ),
    Case(
        holds.CLOUD_LOCKED,
        lambda api, c, rig: _metadata(c, "cloud-access", {"access": "locked"}),
        health="cloud_locked",
        card=task_wakes.CLOUD_LOCKED,
    ),
    Case(
        holds.RUN_HOLD,
        _run_hold,
        health="run_hold",
        card=task_wakes.RUNS_FAILING,
        stopped="run_hold",
        label="Tico stopped ops",
        warned=True,
    ),
    # Before: a bot held by its run budget showed its tasks as its turn, not Paused.
    Case(
        holds.BUDGET,
        _budget,
        health="budget",
        card=task_wakes.RUNAWAY,
        stopped="budget",
        label="300 runs in the last hour",
        warned=True,
    ),
    # Before: a bot at its usage limit with no fallback showed its tasks as its turn, not Paused.
    Case(holds.USAGE_LIMIT, _usage_limit, health="cooling_off", card=task_wakes.USAGE_LIMIT),
    # A task BotOps has because its runs can't start: the claim still takes a person's new run on it.
    Case(holds.CANT_START, _cant_start, card=task_wakes.CANT_START, claimed=True),
    # Before: a quarantine on one task showed that task as the bot's turn.
    Case(holds.TASK_QUARANTINE, _task_quarantine, health="task_quarantine", card=task_wakes.TASK_QUARANTINED),
    # Before: a run its claim could not start read "Saved - queued", and its task the bot's turn.
    Case(holds.CLAIM_HELD, _claim_held, card=task_wakes.CLAIM_HELD, label="Tico couldn't start this run"),
    Case(holds.HANDED_BACK, _handed_back, health="handed_back"),
    Case(holds.BACKOFF, _backoff, health="backoff", label="is pausing"),
]


def _start(api, monkeypatch):
    monkeypatch.setattr(H, "now", lambda: NOW)
    machine = runner(api)
    assign(api, machine, "ops")
    ready(api, machine, ["ops"])
    made = post(api, "tasks", {"owner": "ops", "title": "Review the change", "body": "Please review it."})
    task_id = made.get("task", made).get("id") or made["refs"]["task"]
    with api.app.state.store.transaction() as c:
        H.status_set(c, H.KEEPER, "ops", state="idle", focus="Checked the queue")  # a status row to overlay
        job = c.execute(
            "SELECT j.id FROM jobs j JOIN messages m ON m.id=j.message_id JOIN conversations cv ON "
            f"cv.id=m.conversation_id WHERE j.state='queued' AND {H.MESSAGE_TASK_SQL}=?",
            (task_id,),
        ).fetchone()
    return machine, {"runner_id": machine["runner_id"], "task_id": task_id, "job_id": job["id"]}


def _hand_back(api, machine):
    attempt = claim(api, machine)
    post(
        api,
        f"attempts/{attempt['id']}/complete",
        {"outcome": "interrupted", "last_seq": 0, "text": ""},
        machine["token"],
    )
    ready(api, machine, ["ops"])


@pytest.mark.parametrize("case", CASES, ids=[case.kind for case in CASES])
def test_the_claim_and_every_page_agree_with_holds_why(api, monkeypatch, case):
    machine, rig = _start(api, monkeypatch)
    if case.kind == holds.HANDED_BACK:
        _hand_back(api, machine)
    with api.app.state.store.transaction() as c:
        case.setup(api, c, rig)
    settings = api.app.state.store.settings
    with api.app.state.store.read() as c:
        job = c.execute("SELECT * FROM jobs WHERE id=?", (rig["job_id"],)).fetchone()
        assert holds.why(c, "ops", task_id=rig["task_id"], job=job) == case.kind
        if case.health is not SKIP:
            assert execution.bot_refusal(c, settings, rig["runner_id"], "ops") == case.health
        card = task_wakes.cannot_run(c, "bot:ops") or task_wakes.task_cause(c, rig["task_id"], "bot:ops")
        assert card == case.card
        # The same answers from a pass over many bots and tasks, read together first.
        task_wakes.prime_bots(c, ["bot:ops"])
        task_wakes.prime_holds(c, [rig["task_id"]])
        assert (task_wakes.cannot_run(c, "bot:ops") or task_wakes.task_cause(c, rig["task_id"], "bot:ops")) == card
        on_task = holds.on_tasks(c).get(rig["task_id"], set())
        assert ("ops" in on_task) == (case.kind in holds.ON_TASK)
        label = views.task_wait(c, rig["task_id"], settings)
        assert (holds.warning(c, "bot:ops") is not None) == case.warned
        if case.kind == holds.USAGE_LIMIT:
            assert execution.cooling_off(c, settings, "ops")  # the status is the display of the claim's answer
    if case.label:
        assert case.label.lower() in label.lower()
    row = get(api, "status?bot=ops")["status"]
    assert row.get("stopped") == case.stopped
    assert bool(row.get("backoff")) == (case.kind == holds.BACKOFF)
    ready(api, machine, ["ops"])
    assert bool(claim(api, machine)) == case.claimed


def test_a_person_messaging_a_bot_tico_stopped_is_told_and_a_budget_hold_lets_the_message_through(api, monkeypatch):
    machine, rig = _start(api, monkeypatch)
    with api.app.state.store.transaction() as c:
        _budget(api, c, rig)
    said = post(api, "chat/ops", {"text": "Are you there?"})
    assert "300 runs in the last hour" in said["warning"]
    with api.app.state.store.read() as c:
        assert holds.queued_reason(c, "ops", human=True) is None  # the claim starts a person's message past it
        assert holds.why(c, "ops", kinds=holds.CLAIM_JOB, message={"sender_id": "human:ana"}, claim=True) is None
    ready(api, machine, ["ops"])
    attempt = claim(api, machine)
    assert attempt and attempt["message"]["body"] == "Are you there?"
